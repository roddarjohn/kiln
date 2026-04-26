"""Tests for ingot.queue.

Live-connection paths (:func:`ingot.queue.get_queue`,
:func:`ingot.queue.open_worker_driver`) need a real PostgreSQL,
so they're exercised only in integration tests.  This module
covers the pure-Python helper that adapts SQLAlchemy DSNs to
asyncpg.
"""

from __future__ import annotations

from ingot.queue import _coerce_to_asyncpg_dsn


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
