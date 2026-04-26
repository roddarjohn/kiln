"""Tests for ingot.utils."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from ingot.utils import assert_rowcount, get_object_from_query_or_404


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
