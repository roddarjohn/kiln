"""pgqueuer integration for kiln-generated FastAPI projects.

Two helpers — that's the whole surface kiln contributes to the
queue story.  Everything else (worker run loop, ``@entrypoint``,
CLI) is pgqueuer's own; use it directly per the upstream docs.

* :func:`get_queue` (producer) — wraps the asyncpg connection
  underlying a SQLAlchemy ``AsyncSession`` so jobs enqueue inside
  the *same* transaction as the request's other writes.  The job
  becomes durable when the session commits; if the session rolls
  back, the job is gone.  This is the transactional-outbox
  pattern, and it's the one piece pgqueuer doesn't ship.

* :func:`open_worker_driver` (worker bootstrap) — opens a
  dedicated asyncpg connection from a DSN, coercing SQLAlchemy's
  ``postgresql+asyncpg://`` URL form to plain ``postgresql://``
  so the same env var works for both sides.  Use as the outermost
  ``async with`` in your pgqueuer factory.

Both helpers assume the database is reached through the asyncpg
driver.  Other drivers (psycopg, etc.) would need a parallel
shim and are not supported today.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import asyncpg
from pgqueuer import Queries
from pgqueuer.db import AsyncpgDriver

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_SQLALCHEMY_ASYNCPG_PREFIX = "postgresql+asyncpg://"
_PLAIN_POSTGRES_PREFIX = "postgresql://"


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
    the outermost ``async with`` in the factory you hand to
    ``pgq run``::

        from ingot import open_worker_driver
        from pgqueuer import PgQueuer

        async def main() -> PgQueuer:
            async with open_worker_driver(os.environ["DATABASE_URL"]) as driver:
                pgq = PgQueuer(driver)

                @pgq.entrypoint("ping")
                async def ping(job): ...

                return pgq

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
