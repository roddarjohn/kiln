"""Tests for ingot.filters."""

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import declarative_base

from ingot.filters import apply_filters

Base = declarative_base()


class Post(Base):
    __tablename__ = "post"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    author = Column(String)


class Condition(BaseModel):
    field: Literal["title", "author", "id"]
    op: Literal["eq", "neq", "gt", "gte", "lt", "lte", "contains", "in"] = "eq"
    value: object


class AndExpr(BaseModel):
    and_: list[Condition]


class OrExpr(BaseModel):
    or_: list[Condition]


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(
        "\n", " "
    )


def test_apply_single_condition():
    stmt = apply_filters(
        select(Post), Condition(field="title", value="hi"), Post
    )
    sql = _sql(stmt)
    assert "WHERE" in sql
    assert "post.title = 'hi'" in sql


def test_apply_neq_condition():
    stmt = apply_filters(
        select(Post),
        Condition(field="title", op="neq", value="hi"),
        Post,
    )
    assert "post.title != 'hi'" in _sql(stmt)


def test_apply_and_combiner():
    expr = AndExpr(
        and_=[
            Condition(field="title", value="hi"),
            Condition(field="author", value="alice"),
        ],
    )
    sql = _sql(apply_filters(select(Post), expr, Post))
    assert "post.title = 'hi'" in sql
    assert "post.author = 'alice'" in sql
    assert " AND " in sql


def test_apply_or_combiner():
    expr = OrExpr(
        or_=[
            Condition(field="title", value="hi"),
            Condition(field="author", value="alice"),
        ],
    )
    sql = _sql(apply_filters(select(Post), expr, Post))
    assert " OR " in sql


def test_empty_combiner_returns_stmt_unchanged():
    stmt_in = select(Post)
    stmt_out = apply_filters(stmt_in, AndExpr(and_=[]), Post)
    assert "WHERE" not in _sql(stmt_out)


def test_node_without_field_returns_stmt_unchanged():
    class Empty(BaseModel):
        pass

    stmt_out = apply_filters(select(Post), Empty(), Post)
    assert "WHERE" not in _sql(stmt_out)


def test_gt_operator():
    stmt = apply_filters(
        select(Post),
        Condition(field="id", op="gt", value=5),
        Post,
    )
    assert "post.id > 5" in _sql(stmt)


def test_contains_operator():
    stmt = apply_filters(
        select(Post),
        Condition(field="title", op="contains", value="hi"),
        Post,
    )
    sql = _sql(stmt)
    assert "post.title LIKE" in sql
