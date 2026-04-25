"""Tests for ingot.queue.

Live-connection paths (:func:`ingot.queue.get_queue`,
:func:`ingot.queue.open_worker_driver`) need a real PostgreSQL,
so they're exercised only in integration tests.  This module
covers the pure-Python helpers.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import timedelta
from typing import Any

import pytest

from ingot import register_module_tasks, task
from ingot.queue import _coerce_to_asyncpg_dsn, _entrypoint_kwargs


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


class TestTaskDecorator:
    """Tests for the :func:`ingot.task` marker decorator."""

    def test_bare_decorator_uses_fn_name(self):
        """``@task`` without parens names the entrypoint after the fn."""

        @task
        async def ping(_):
            return "pong"

        meta = ping.__pgqueuer_task__
        assert meta["name"] == "ping"
        assert meta["concurrency_limit"] is None
        assert meta["retry_timer_seconds"] is None

    def test_kwargs_decorator_captures_tuning(self):
        """``@task(...)`` captures tuning kwargs into the metadata."""

        @task(
            concurrency_limit=4,
            requests_per_second=10.0,
            retry_timer_seconds=30,
            serialized_dispatch=True,
        )
        async def send_welcome(_):
            return None

        meta = send_welcome.__pgqueuer_task__
        assert meta["name"] == "send_welcome"
        assert meta["concurrency_limit"] == 4
        assert meta["requests_per_second"] == 10.0
        assert meta["retry_timer_seconds"] == 30
        assert meta["serialized_dispatch"] is True

    def test_explicit_name_override(self):
        """``name=`` overrides the default fn-name."""

        @task(name="legacy.ping")
        async def ping_v2(_):
            return None

        assert ping_v2.__pgqueuer_task__["name"] == "legacy.ping"

    def test_decorator_preserves_callable(self):
        """The decorated fn is still directly callable in tests."""

        @task
        async def ping(payload):
            return payload * 2

        assert asyncio.run(ping(21)) == 42

    def test_rejects_sync_function(self):
        """Sync fns can't be pgqueuer entrypoints — fail loudly."""
        with pytest.raises(TypeError, match="async functions"):

            @task
            def not_async(_):
                pass


class TestEntrypointKwargs:
    """Tests for the meta→kwargs translation."""

    def test_drops_none_fields(self):
        """``None`` entries are dropped so pgqueuer defaults stay."""
        meta = {
            "name": "x",
            "concurrency_limit": None,
            "requests_per_second": None,
            "retry_timer_seconds": None,
            "serialized_dispatch": None,
        }
        assert _entrypoint_kwargs(meta) == {}

    def test_retry_timer_becomes_timedelta(self):
        """``retry_timer_seconds`` translates to a timedelta."""
        meta = {
            "name": "x",
            "concurrency_limit": None,
            "requests_per_second": None,
            "retry_timer_seconds": 30,
            "serialized_dispatch": None,
        }
        result = _entrypoint_kwargs(meta)
        assert result == {"retry_timer": timedelta(seconds=30)}

    def test_all_fields_pass_through(self):
        """Set fields land on the right pgqueuer kwarg names."""
        meta = {
            "name": "x",
            "concurrency_limit": 2,
            "requests_per_second": 5.0,
            "retry_timer_seconds": 10,
            "serialized_dispatch": True,
        }
        assert _entrypoint_kwargs(meta) == {
            "concurrency_limit": 2,
            "requests_per_second": 5.0,
            "retry_timer": timedelta(seconds=10),
            "serialized_dispatch": True,
        }


class _FakePgQueuer:
    """A pgq stand-in capturing entrypoint registrations for assertion.

    Avoids spinning up a real pgqueuer (which wants a driver and a
    DB).  Mirrors the shape of :meth:`pgqueuer.PgQueuer.entrypoint`:
    ``entrypoint(name, **kwargs)`` returns a decorator that
    accepts the fn.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], Any]] = []

    def entrypoint(self, name: str, **kwargs: Any):
        def decorator(fn):
            self.calls.append((name, kwargs, fn))
            return fn

        return decorator


def _make_module(name: str, **attrs: Any) -> types.ModuleType:
    """Build a transient module so register_module_tasks has something
    to walk.  Inserted into ``sys.modules`` so ``__module__`` checks
    align with what real imports would produce.
    """
    module = types.ModuleType(name)
    sys.modules[name] = module
    for attr_name, attr_value in attrs.items():
        if hasattr(attr_value, "__module__"):
            attr_value.__module__ = name
        setattr(module, attr_name, attr_value)
    return module


class TestRegisterModuleTasks:
    """Tests for :func:`ingot.register_module_tasks`."""

    def test_registers_tagged_fns(self):
        """Every @task-decorated fn gets a pgq.entrypoint call."""

        @task
        async def ping(_):
            return None

        @task(concurrency_limit=2)
        async def bake(_):
            return None

        module = _make_module("_test_register_basic", ping=ping, bake=bake)
        pgq = _FakePgQueuer()

        registered = register_module_tasks(pgq, module)

        assert sorted(registered) == ["bake", "ping"]
        names = sorted(call[0] for call in pgq.calls)
        assert names == ["bake", "ping"]
        # bake had a tuning kwarg; ping did not.
        bake_call = next(c for c in pgq.calls if c[0] == "bake")
        ping_call = next(c for c in pgq.calls if c[0] == "ping")
        assert bake_call[1] == {"concurrency_limit": 2}
        assert ping_call[1] == {}

    def test_skips_untagged_callables(self):
        """Plain async helpers in the module aren't registered."""

        @task
        async def ping(_):
            return None

        async def helper(_):  # not decorated
            return None

        module = _make_module("_test_register_skip", ping=ping, helper=helper)
        pgq = _FakePgQueuer()

        registered = register_module_tasks(pgq, module)

        assert registered == ["ping"]

    def test_skips_imported_tasks(self):
        """Re-exported tasks (defined elsewhere) are not double-registered."""

        @task
        async def ping(_):
            return None

        # Define it in module A, re-export from module B.
        _make_module("_test_register_origin", ping=ping)
        re_export = _make_module("_test_register_re_export", ping=ping)
        # Now ping.__module__ is "_test_register_re_export" because
        # _make_module mutates __module__ on the last insertion.
        # Reset it to the origin so the import filter has work to do.
        ping.__module__ = "_test_register_origin"

        pgq = _FakePgQueuer()
        registered = register_module_tasks(pgq, re_export)

        assert registered == []

    def test_rejects_duplicate_names(self):
        """Two tasks with the same entrypoint name explode at register."""

        @task(name="dup")
        async def first(_):
            return None

        @task(name="dup")
        async def second(_):
            return None

        module = _make_module("_test_register_dup", first=first, second=second)
        pgq = _FakePgQueuer()

        with pytest.raises(ValueError, match="Duplicate"):
            register_module_tasks(pgq, module)

    def test_retry_timer_translated(self):
        """retry_timer_seconds threads through as a timedelta."""

        @task(retry_timer_seconds=15)
        async def slow(_):
            return None

        module = _make_module("_test_register_retry", slow=slow)
        pgq = _FakePgQueuer()

        register_module_tasks(pgq, module)

        (call,) = pgq.calls
        assert call[1] == {"retry_timer": timedelta(seconds=15)}
