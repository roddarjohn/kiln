"""Tests for ingot.pagination."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import declarative_base

from ingot.pagination import apply_keyset_pagination, apply_offset_pagination

Base = declarative_base()


class Post(Base):
    __tablename__ = "post"
    id = Column(Integer, primary_key=True)
    title = Column(String)


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(
        "\n", " "
    )


def test_keyset_no_cursor_just_limits():
    stmt, size = apply_keyset_pagination(
        select(Post),
        Post,
        cursor=None,
        cursor_field="id",
        page_size=10,
        max_page_size=100,
    )
    sql = _sql(stmt)
    assert "WHERE" not in sql
    assert "LIMIT 11" in sql
    assert size == 10


def test_keyset_with_cursor_adds_where():
    stmt, _ = apply_keyset_pagination(
        select(Post),
        Post,
        cursor=5,
        cursor_field="id",
        page_size=10,
        max_page_size=100,
    )
    sql = _sql(stmt)
    assert "post.id > 5" in sql
    assert "LIMIT 11" in sql


def test_keyset_clamps_page_size_to_max():
    _, size = apply_keyset_pagination(
        select(Post),
        Post,
        cursor=None,
        cursor_field="id",
        page_size=500,
        max_page_size=100,
    )
    assert size == 100


async def test_offset_returns_total_and_rows():
    count_result = MagicMock()
    count_result.scalar_one.return_value = 42

    row_result = MagicMock()
    row_result.scalars.return_value = ["a", "b", "c"]

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, row_result])

    total, rows = await apply_offset_pagination(
        db, select(Post), offset=0, limit=10, max_page_size=100
    )
    assert total == 42
    assert rows == ["a", "b", "c"]
    assert db.execute.await_count == 2


async def test_offset_clamps_limit_to_max():
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    row_result = MagicMock()
    row_result.scalars.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, row_result])

    await apply_offset_pagination(
        db, select(Post), offset=0, limit=9999, max_page_size=50
    )

    (row_stmt,) = db.execute.await_args_list[1].args
    assert "LIMIT 50" in _sql(row_stmt)
