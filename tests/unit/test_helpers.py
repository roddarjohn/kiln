"""Tests for kiln.generators._helpers."""

from __future__ import annotations

import pytest

from kiln.config.schema import DatabaseConfig
from kiln.generators._helpers import resolve_db_session


def test_resolve_db_session_no_databases():
    assert resolve_db_session(None, []) == ("db.session", "get_db")


def test_resolve_db_session_default_database():
    dbs = [
        DatabaseConfig(key="primary", default=True),
        DatabaseConfig(key="reports", default=False),
    ]
    assert resolve_db_session(None, dbs) == (
        "db.primary_session",
        "get_primary_db",
    )


def test_resolve_db_session_explicit_key():
    dbs = [
        DatabaseConfig(key="primary", default=True),
        DatabaseConfig(key="reports", default=False),
    ]
    assert resolve_db_session("reports", dbs) == (
        "db.reports_session",
        "get_reports_db",
    )


def test_resolve_db_session_no_default_raises():
    dbs = [DatabaseConfig(key="primary", default=False)]
    with pytest.raises(ValueError, match="default=True"):
        resolve_db_session(None, dbs)


def test_resolve_db_session_unknown_key_raises():
    dbs = [DatabaseConfig(key="primary", default=True)]
    with pytest.raises(ValueError, match="No database with key"):
        resolve_db_session("missing", dbs)
