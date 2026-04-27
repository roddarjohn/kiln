"""Tests for ingot.utils."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from ingot.utils import (
    assert_rowcount,
    get_object_from_query_or_404,
    run_once,
)

# ---------------------------------------------------------------------------
# get_object_from_query_or_404 / assert_rowcount
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


def test_assert_rowcount_passes_when_match():
    result = MagicMock()
    result.rowcount = 1
    assert_rowcount(result)


def test_assert_rowcount_raises_on_mismatch():
    result = MagicMock()
    result.rowcount = 0
    with pytest.raises(HTTPException) as exc:
        assert_rowcount(result)
    assert exc.value.status_code == 404


def test_assert_rowcount_custom_expected():
    result = MagicMock()
    result.rowcount = 3
    assert_rowcount(result, expected=3)


def test_assert_rowcount_custom_status_and_detail():
    result = MagicMock()
    result.rowcount = 0
    with pytest.raises(HTTPException) as exc:
        assert_rowcount(result, status_code=409, detail="conflict")
    assert exc.value.status_code == 409
    assert exc.value.detail == "conflict"


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
