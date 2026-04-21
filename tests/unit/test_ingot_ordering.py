"""Tests for ingot.ordering."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import declarative_base

from ingot.ordering import apply_ordering

Base = declarative_base()


class Post(Base):
    __tablename__ = "post"
    id = Column(Integer, primary_key=True)
    title = Column(String)


class PostSortField(StrEnum):
    TITLE = "title"
    ID = "id"


class SortClause(BaseModel):
    field: PostSortField
    dir: str = "asc"


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(
        "\n", " "
    )


def test_default_field_asc_when_no_clauses():
    stmt = apply_ordering(select(Post), None, Post, default_field="id")
    assert "ORDER BY post.id ASC" in _sql(stmt)


def test_default_field_desc():
    stmt = apply_ordering(
        select(Post), None, Post, default_field="id", default_dir="desc"
    )
    assert "ORDER BY post.id DESC" in _sql(stmt)


def test_empty_list_uses_default():
    stmt = apply_ordering(select(Post), [], Post, default_field="title")
    assert "ORDER BY post.title ASC" in _sql(stmt)


def test_single_sort_clause_asc():
    stmt = apply_ordering(
        select(Post),
        [SortClause(field=PostSortField.TITLE, dir="asc")],
        Post,
        default_field="id",
    )
    assert "ORDER BY post.title ASC" in _sql(stmt)


def test_single_sort_clause_desc():
    stmt = apply_ordering(
        select(Post),
        [SortClause(field=PostSortField.TITLE, dir="desc")],
        Post,
        default_field="id",
    )
    assert "ORDER BY post.title DESC" in _sql(stmt)


def test_multiple_sort_clauses_preserve_order():
    stmt = apply_ordering(
        select(Post),
        [
            SortClause(field=PostSortField.TITLE, dir="asc"),
            SortClause(field=PostSortField.ID, dir="desc"),
        ],
        Post,
        default_field="id",
    )
    sql = _sql(stmt)
    assert "ORDER BY post.title ASC, post.id DESC" in sql
