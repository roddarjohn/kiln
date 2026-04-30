"""Tests for ingot.pagination."""

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


def test_offset_builds_paginated_and_count_stmts():
    paginated_stmt, count_stmt, size = apply_offset_pagination(
        select(Post), offset=20, limit=10, max_page_size=100
    )
    paginated_sql = _sql(paginated_stmt)
    assert "LIMIT 10" in paginated_sql
    assert "OFFSET 20" in paginated_sql
    assert "count(*)" in _sql(count_stmt).lower()
    assert size == 10


def test_offset_clamps_limit_to_max():
    paginated_stmt, _, size = apply_offset_pagination(
        select(Post), offset=0, limit=9999, max_page_size=50
    )
    assert size == 50
    assert "LIMIT 50" in _sql(paginated_stmt)


