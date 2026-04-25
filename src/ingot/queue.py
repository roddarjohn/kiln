"""pgqueuer integration for kiln-generated FastAPI projects.

Three pieces, one for each side of the queue:

* :func:`get_queue` (producer) — wraps the asyncpg connection
  underlying a SQLAlchemy ``AsyncSession`` so jobs enqueue inside
  the *same* transaction as the request's other writes.  The job
  becomes durable when the session commits; if the session rolls
  back, the job is gone.  This is the transactional-outbox
  pattern.

* :func:`task` (consumer-side decorator) — stamps an async
  function with pgqueuer-entrypoint metadata.  Users decorate
  the functions they want pgqueuer to run; the generated worker
  discovers them via :func:`register_module_tasks`.  Tuning
  lives next to the function, not in jsonnet.

* :func:`open_worker_driver` (worker bootstrap) — opens a
  dedicated asyncpg connection for the worker process.  Workers
  are long-lived and must own their connection (pgqueuer
  ``LISTEN``s on it), so they cannot share the request pool.

* :func:`register_module_tasks` (worker bootstrap) — walks a
  module, finds every :func:`task`-decorated callable, and
  registers each on the supplied :class:`pgqueuer.PgQueuer`.

All four assume the database is reached through the asyncpg
driver (``postgresql+asyncpg://...`` for SQLAlchemy).  Other
drivers are not supported.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any, overload

import asyncpg
from pgqueuer import Queries
from pgqueuer.db import AsyncpgDriver

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from types import ModuleType

    from pgqueuer import PgQueuer
    from sqlalchemy.ext.asyncio import AsyncSession

_SQLALCHEMY_ASYNCPG_PREFIX = "postgresql+asyncpg://"
_PLAIN_POSTGRES_PREFIX = "postgresql://"

#: Attribute name kiln stamps on ``@task``-decorated functions.
#: Used by :func:`register_module_tasks` to discover tasks at
#: worker boot.  Treated as private — read it through the helper,
#: not directly.
_TASK_MARKER = "__pgqueuer_task__"


async def get_queue(session: AsyncSession) -> Queries:
    """Return a :class:`pgqueuer.Queries` bound to *session*'s connection.

    Calls to ``await queue.enqueue(...)`` issue SQL on the same
    asyncpg connection the SQLAlchemy session is using, so they
    join the session's transaction.  Commit the session and the
    job is durable; roll back and it never existed.

    Args:
        session: A SQLAlchemy async session backed by the asyncpg
            driver.  The session is checked out from a connection
            so the underlying ``asyncpg.Connection`` can be
            unwrapped.

    Returns:
        A :class:`pgqueuer.Queries` whose driver wraps the
        session's asyncpg connection.

    """
    raw_connection = await session.connection()
    asyncpg_wrapper = await raw_connection.get_raw_connection()
    driver_connection = asyncpg_wrapper.driver_connection
    if driver_connection is None:
        msg = (
            "Session is not backed by a live driver connection — "
            "ensure the engine uses postgresql+asyncpg:// and the "
            "session is checked out before calling get_queue()."
        )
        raise RuntimeError(msg)
    return Queries(AsyncpgDriver(driver_connection))


def _coerce_to_asyncpg_dsn(dsn: str) -> str:
    """Strip SQLAlchemy's ``+asyncpg`` prefix if present.

    SQLAlchemy URLs use ``postgresql+asyncpg://...``; raw asyncpg
    wants ``postgresql://...``.  Other prefixes pass through
    unchanged, so a caller who already supplies a plain DSN
    needs no special-casing.
    """
    if dsn.startswith(_SQLALCHEMY_ASYNCPG_PREFIX):
        return _PLAIN_POSTGRES_PREFIX + dsn[len(_SQLALCHEMY_ASYNCPG_PREFIX) :]
    return dsn


@asynccontextmanager
async def open_worker_driver(dsn: str) -> AsyncIterator[AsyncpgDriver]:
    """Open a dedicated worker connection and yield an :class:`AsyncpgDriver`.

    Workers need a long-lived connection of their own so pgqueuer
    can ``LISTEN`` for new-job notifications on it.  Use this as
    the outermost ``async with`` in your worker entrypoint; the
    connection is closed when the block exits.

    Args:
        dsn: A PostgreSQL DSN.  Either plain
            (``postgresql://user:pw@host/db``) or SQLAlchemy-shaped
            (``postgresql+asyncpg://...``) — the latter is rewritten
            so the same env var works for both the request path and
            the worker.

    Yields:
        An :class:`AsyncpgDriver` wrapping the freshly-opened
        connection.

    """
    connection = await asyncpg.connect(_coerce_to_asyncpg_dsn(dsn))
    try:
        yield AsyncpgDriver(connection)
    finally:
        await connection.close()


@overload
def task[F: Callable[..., Awaitable[Any]]](fn: F) -> F: ...


@overload
def task[F: Callable[..., Awaitable[Any]]](
    fn: None = ...,
    *,
    name: str | None = ...,
    concurrency_limit: int | None = ...,
    requests_per_second: float | None = ...,
    retry_timer_seconds: float | None = ...,
    serialized_dispatch: bool | None = ...,
) -> Callable[[F], F]: ...


def task[F: Callable[..., Awaitable[Any]]](
    fn: F | None = None,
    *,
    name: str | None = None,
    concurrency_limit: int | None = None,
    requests_per_second: float | None = None,
    retry_timer_seconds: float | None = None,
    serialized_dispatch: bool | None = None,
) -> F | Callable[[F], F]:
    """Mark an async function as a pgqueuer task.

    Use bare for default tuning, or with kwargs to override one
    or more of pgqueuer's :meth:`PgQueuer.entrypoint` defaults::

        @task
        async def ping(job): ...

        @task(concurrency_limit=4, retry_timer_seconds=30)
        async def send_welcome(job): ...

        @task(name="legacy.ping")  # entrypoint name != fn name
        async def ping_v2(job): ...

    The decorator stamps :data:`_TASK_MARKER` metadata on the
    function and otherwise leaves it untouched, so the function
    is still directly callable in tests::

        await ping(fake_job)

    Args:
        fn: The async function to decorate.  Set automatically
            when used bare; ``None`` when called with kwargs.
        name: Override the pgqueuer entrypoint name.  Defaults
            to ``fn.__name__``.
        concurrency_limit: See
            :meth:`pgqueuer.PgQueuer.entrypoint`.
        requests_per_second: See
            :meth:`pgqueuer.PgQueuer.entrypoint`.
        retry_timer_seconds: Translated to a :class:`timedelta`
            and passed as ``retry_timer``.
        serialized_dispatch: See
            :meth:`pgqueuer.PgQueuer.entrypoint`.

    Returns:
        The decorated function (unchanged), or a decorator if
        called with kwargs.

    """

    def wrap(target: F) -> F:
        if not inspect.iscoroutinefunction(target):
            msg = (
                f"@task can only decorate async functions; "
                f"{target.__qualname__} is not."
            )
            raise TypeError(msg)
        target.__pgqueuer_task__ = {  # type: ignore[attr-defined]
            "name": name or target.__name__,
            "concurrency_limit": concurrency_limit,
            "requests_per_second": requests_per_second,
            "retry_timer_seconds": retry_timer_seconds,
            "serialized_dispatch": serialized_dispatch,
        }
        return target

    return wrap if fn is None else wrap(fn)


def _entrypoint_kwargs(meta: dict[str, Any]) -> dict[str, Any]:
    """Translate :func:`task` metadata into entrypoint kwargs.

    ``None`` entries are dropped so pgqueuer's own defaults kick
    in for unspecified fields.  ``retry_timer_seconds`` is
    converted to a :class:`timedelta` because that's what
    pgqueuer's API expects.
    """
    kwargs: dict[str, Any] = {}
    if meta["concurrency_limit"] is not None:
        kwargs["concurrency_limit"] = meta["concurrency_limit"]
    if meta["requests_per_second"] is not None:
        kwargs["requests_per_second"] = meta["requests_per_second"]
    if meta["retry_timer_seconds"] is not None:
        kwargs["retry_timer"] = timedelta(seconds=meta["retry_timer_seconds"])
    if meta["serialized_dispatch"] is not None:
        kwargs["serialized_dispatch"] = meta["serialized_dispatch"]
    return kwargs


def register_module_tasks(
    pgq: PgQueuer,
    module: ModuleType,
) -> list[str]:
    """Register every :func:`task`-decorated fn in *module* on *pgq*.

    Walks the module's public attributes, picks out callables
    bearing the :func:`task` marker, and calls
    :meth:`pgq.entrypoint` per match — passing through the
    tuning kwargs the decorator captured.

    Functions imported from elsewhere are skipped: only those
    whose module of definition matches *module* are registered.
    This avoids accidentally double-registering a task that's
    re-exported into multiple namespaces.

    Args:
        pgq: The :class:`pgqueuer.PgQueuer` to register against.
        module: The user-authored module containing
            ``@task``-decorated coroutine functions.

    Returns:
        The pgqueuer entrypoint names that were registered, in
        registration order.

    Raises:
        ValueError: If two functions in *module* share the same
            entrypoint name.  Catches the typo where two tasks
            both default to ``__name__ == "process"``.

    """
    registered: list[str] = []
    seen: set[str] = set()
    for member_name, member in inspect.getmembers(module):
        if member_name.startswith("_"):
            continue
        meta = getattr(member, _TASK_MARKER, None)
        if meta is None:
            continue
        if getattr(member, "__module__", None) != module.__name__:
            continue
        entrypoint_name: str = meta["name"]
        if entrypoint_name in seen:
            msg = (
                f"Duplicate pgqueuer entrypoint name "
                f"{entrypoint_name!r} in module {module.__name__!r}; "
                f"each @task must produce a unique name."
            )
            raise ValueError(msg)
        seen.add(entrypoint_name)
        pgq.entrypoint(entrypoint_name, **_entrypoint_kwargs(meta))(member)
        registered.append(entrypoint_name)
    return registered
