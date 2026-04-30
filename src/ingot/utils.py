"""General-purpose runtime utilities used by generated apps.

Three unrelated concerns share this module by convention --
:func:`get_object_from_query_or_404` used by every read-or-mutate
CRUD handler, the :func:`run_once` decorator used by the
generated telemetry init, and :func:`compile_query` for tests
that assert against rendered SQL.  Bundling them here keeps the
public ``ingot`` surface flat enough that consumers learn one
import path (``from ingot.utils import ...``) for everything that
doesn't fit under a more specific submodule.
"""

import functools
from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.dialects import postgresql, sqlite

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Dialect
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import ClauseElement


# -------------------------------------------------------------------
# HTTP-status row-lookup guards
# -------------------------------------------------------------------


async def get_object_from_query_or_404(
    db: AsyncSession,
    stmt: Any,
    *,
    detail: str = "Not found",
) -> Any:
    """Execute *stmt* and return the first row, or raise HTTP 404.

    Args:
        db: The async database session.
        stmt: A SQLAlchemy selectable statement.
        detail: The error message for the 404 response.

    Returns:
        The first row from the result set.

    Raises:
        HTTPException: With status 404 when no row is found.

    """
    result = await db.execute(stmt)
    row = result.scalars().one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )

    return row


# -------------------------------------------------------------------
# Once-only execution
# -------------------------------------------------------------------


def run_once(fn: Callable[..., None]) -> Callable[..., None]:
    """Idempotency decorator: run ``fn`` once, ignore later calls.

    Unlike :func:`functools.cache`, the gate is *argument-blind* --
    a second call with a different argument set is still a no-op,
    not a fresh execution keyed on the new args.  This is the
    correct shape for one-shot setup functions: calling
    ``init_telemetry(app1)`` then ``init_telemetry(app2)`` must not
    install a second tracer provider or instrument a second
    FastAPI app.

    The wrapped function's return value is discarded so callers
    can't accidentally rely on a "first call's return" pattern,
    which would leak the gate to the public API surface.
    """
    called = False

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        nonlocal called

        if called:
            return

        called = True
        fn(*args, **kwargs)

    return wrapper


# -------------------------------------------------------------------
# SQL inspection helper (test-only, but ships in ingot so consumers
# can reuse it)
# -------------------------------------------------------------------


_DIALECTS: dict[str, Callable[[], Dialect]] = {
    "postgres": postgresql.dialect,
    "postgresql": postgresql.dialect,
    "sqlite": sqlite.dialect,
}
"""Named dialect factories accepted by :func:`compile_query`.
``"postgres"`` and ``"postgresql"`` are aliases."""


def compile_query(
    stmt: ClauseElement,
    *,
    dialect: Literal["postgres", "postgresql", "sqlite"] | None = None,
    literal_binds: bool = True,
) -> str:
    """Render a SQLAlchemy statement to a single SQL string.

    Test-oriented helper: tests that assert against generated SQL
    (locking modifiers, where-clause shape, computed expressions)
    repeatedly spell ``str(stmt.compile(compile_kwargs={"literal_binds":
    True}))`` and frequently need a Postgres dialect to surface
    pg-specific syntax (``SKIP LOCKED``, ``ON CONFLICT``, ...).
    Centralising the boilerplate keeps assertions readable and
    avoids per-test imports of the dialect submodule.

    Args:
        stmt: Any SQLAlchemy clause -- ``select()``, ``insert()``,
            ``update()``, raw ``text()``, etc.
        dialect: Optional dialect name.  ``None`` (the default) uses
            SQLAlchemy's generic compiler, which strips
            dialect-specific clauses (``FOR UPDATE`` survives;
            ``SKIP LOCKED`` does not).  Pass ``"postgres"`` to
            render Postgres SQL or ``"sqlite"`` for sqlite.
        literal_binds: When ``True`` (the default), bound parameters
            render inline -- ``WHERE id = 'abc'`` rather than
            ``WHERE id = :id_1``.  Set ``False`` to inspect the
            parameter map separately (via ``stmt.compile().params``).

    Returns:
        Compiled SQL as a string.

    """
    compile_kwargs: dict[str, Any] = (
        {"literal_binds": True} if literal_binds else {}
    )
    bound_dialect: Dialect | None = (
        _DIALECTS[dialect]() if dialect is not None else None
    )
    return str(
        stmt.compile(dialect=bound_dialect, compile_kwargs=compile_kwargs)
    )
