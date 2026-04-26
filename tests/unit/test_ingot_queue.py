"""Tests for ingot.queue.

Live PostgreSQL is not available in unit tests, so the SQLAlchemy
session and asyncpg connection are mocked.  Integration tests
exercise the helpers against a real database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pgqueuer import Queries
from pgqueuer.db import AsyncpgDriver

from ingot.queue import (
    _coerce_to_asyncpg_dsn,
    get_queue,
    open_worker_driver,
)


class TestCoerceToAsyncpgDsn:
    """Tests for :func:`ingot.queue._coerce_to_asyncpg_dsn`."""

    def test_strips_sqlalchemy_prefix(self):
        """``postgresql+asyncpg://`` becomes ``postgresql://``."""
        result = _coerce_to_asyncpg_dsn(
            "postgresql+asyncpg://user:pw@host:5432/db"
        )
        assert result == "postgresql://user:pw@host:5432/db"

    def test_plain_postgresql_unchanged(self):
        """A plain ``postgresql://`` DSN passes through unchanged."""
        dsn = "postgresql://user:pw@host:5432/db"
        assert _coerce_to_asyncpg_dsn(dsn) == dsn

    def test_other_prefix_unchanged(self):
        """Non-postgresql prefixes pass through (asyncpg will reject)."""
        dsn = "postgres://user@host/db"
        assert _coerce_to_asyncpg_dsn(dsn) == dsn

    def test_only_replaces_leading_prefix(self):
        """The replacement is anchored at the start, not anywhere."""
        dsn = "postgresql://host/db?app=postgresql+asyncpg"
        assert _coerce_to_asyncpg_dsn(dsn) == dsn


def _fake_session(driver_connection: object) -> AsyncMock:
    """Build a SQLAlchemy session double exposing *driver_connection*.

    Mirrors the chain ``await session.connection()``,
    ``await raw_connection.get_raw_connection()``,
    ``wrapper.driver_connection`` that :func:`get_queue` walks.
    """
    wrapper = MagicMock()
    wrapper.driver_connection = driver_connection

    raw = MagicMock()
    raw.get_raw_connection = AsyncMock(return_value=wrapper)

    session = AsyncMock()
    session.connection = AsyncMock(return_value=raw)
    return session


class TestGetQueue:
    """Tests for :func:`ingot.queue.get_queue`."""

    @pytest.mark.asyncio
    async def test_returns_queries_bound_to_session_connection(self):
        """The driver wraps the session's underlying asyncpg connection."""
        fake_conn = MagicMock(name="asyncpg_connection")
        session = _fake_session(fake_conn)

        with patch("ingot.queue.AsyncpgDriver") as driver_cls:
            queue = await get_queue(session)

        # The driver was constructed against the session's raw connection.
        driver_cls.assert_called_once_with(fake_conn)
        assert isinstance(queue, Queries)

    @pytest.mark.asyncio
    async def test_raises_when_driver_connection_is_none(self):
        """``None`` driver connection means the session isn't checked out."""
        session = _fake_session(None)

        with pytest.raises(RuntimeError, match="postgresql\\+asyncpg"):
            await get_queue(session)


class TestOpenWorkerDriver:
    """Tests for :func:`ingot.queue.open_worker_driver`."""

    @pytest.mark.asyncio
    async def test_opens_connection_and_yields_driver(self):
        """Yields an AsyncpgDriver; closes the connection on exit."""
        fake_conn = MagicMock(name="asyncpg_connection")
        fake_conn.close = AsyncMock()

        with patch(
            "ingot.queue.asyncpg.connect",
            new=AsyncMock(return_value=fake_conn),
        ) as connect:
            async with open_worker_driver(
                "postgresql+asyncpg://user@host/db"
            ) as driver:
                assert isinstance(driver, AsyncpgDriver)

        # DSN was coerced before being handed to asyncpg.
        connect.assert_awaited_once_with("postgresql://user@host/db")
        # Connection was closed on context exit.
        fake_conn.close.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_closes_connection_even_when_body_raises(self):
        """The ``finally`` closes the connection on inner exceptions."""
        fake_conn = MagicMock(name="asyncpg_connection")
        fake_conn.close = AsyncMock()

        async def explode() -> None:
            async with open_worker_driver("postgresql://host/db"):
                msg = "boom"
                raise ValueError(msg)

        with (
            patch(
                "ingot.queue.asyncpg.connect",
                new=AsyncMock(return_value=fake_conn),
            ),
            pytest.raises(ValueError, match="boom"),
        ):
            await explode()

        fake_conn.close.assert_awaited_once_with()
