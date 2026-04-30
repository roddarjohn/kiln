"""Tests for ingot.utils."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, Integer, MetaData, String, Table, select

from ingot.utils import (
    compile_query,
    get_object_from_query_or_404,
    run_once,
)

# ---------------------------------------------------------------------------
# get_object_from_query_or_404
# ---------------------------------------------------------------------------


def _mock_db(row: object) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


async def test_get_object_returns_row_when_found():
    db = _mock_db(row="hello")
    value = await get_object_from_query_or_404(db, stmt=object())
    assert value == "hello"


async def test_get_object_raises_404_when_missing():
    db = _mock_db(row=None)

    with pytest.raises(HTTPException) as exc:
        await get_object_from_query_or_404(db, stmt=object())

    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


async def test_get_object_custom_detail():
    db = _mock_db(row=None)

    with pytest.raises(HTTPException) as exc:
        await get_object_from_query_or_404(db, stmt=object(), detail="no post")

    assert exc.value.detail == "no post"


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


def test_run_once_runs_first_call():
    calls: list[tuple] = []

    @run_once
    def fn(*args, **kwargs):
        calls.append((args, kwargs))

    fn(1, 2, k="v")
    assert calls == [((1, 2), {"k": "v"})]


def test_run_once_ignores_second_call():
    calls: list[int] = []

    @run_once
    def fn(x):
        calls.append(x)

    fn(1)
    fn(2)
    fn(3)
    assert calls == [1]


def test_run_once_argument_blind():
    # Distinguishes ``run_once`` from ``functools.cache``: a second
    # call with *different* args is still a no-op rather than a
    # fresh execution keyed on the new args.
    calls: list[int] = []

    @run_once
    def fn(x):
        calls.append(x)

    fn(1)
    fn(99)
    assert calls == [1]


def test_run_once_returns_none_even_when_wrapped_returns_value():
    @run_once
    def fn():
        return 42

    assert fn() is None


def test_run_once_preserves_metadata():
    @run_once
    def my_fn(x: int) -> None:
        """My docstring."""

    assert my_fn.__name__ == "my_fn"
    assert my_fn.__doc__ == "My docstring."


def test_run_once_isolates_state_per_decoration():
    # Two distinct decorated functions must not share the gate;
    # otherwise calling one would silently disable the other.
    a_calls: list[int] = []
    b_calls: list[int] = []

    @run_once
    def a():
        a_calls.append(1)

    @run_once
    def b():
        b_calls.append(1)

    a()
    a()
    b()
    b()
    assert a_calls == [1]
    assert b_calls == [1]


# ---------------------------------------------------------------------------
# compile_query
# ---------------------------------------------------------------------------


_users = Table(
    "users",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("name", String),
)


def test_compile_query_inlines_bind_params_by_default():
    sql = compile_query(select(_users).where(_users.c.id == 7))
    # Generic dialect; literal_binds=True by default.
    assert "7" in sql
    assert ":id_1" not in sql


def test_compile_query_keeps_binds_when_literal_binds_false():
    sql = compile_query(
        select(_users).where(_users.c.id == 7),
        literal_binds=False,
    )
    assert ":id_1" in sql


def test_compile_query_postgres_renders_skip_locked():
    stmt = (
        select(_users).where(_users.c.id == 1).with_for_update(skip_locked=True)
    )
    sql = compile_query(stmt, dialect="postgres").upper()
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql


def test_compile_query_default_dialect_drops_skip_locked():
    # The generic dialect doesn't know about ``SKIP LOCKED`` -- this
    # test guards the documented contract that callers must pass
    # ``dialect="postgres"`` for the modifier to render.
    stmt = (
        select(_users).where(_users.c.id == 1).with_for_update(skip_locked=True)
    )
    sql = compile_query(stmt).upper()
    assert "SKIP LOCKED" not in sql


def test_compile_query_postgresql_alias():
    stmt = select(_users).with_for_update(skip_locked=True)
    sql = compile_query(stmt, dialect="postgresql").upper()
    assert "SKIP LOCKED" in sql
