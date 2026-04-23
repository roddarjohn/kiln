"""Filter-clause construction for typed Pydantic filter trees."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import and_, or_

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import BaseModel
    from sqlalchemy import Select
    from sqlalchemy.sql.elements import ColumnElement


FilterOp = Literal[
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "starts_with",
    "in",
]
"""Operator keys accepted by a condition node's ``op`` field.

Callers' Pydantic condition models should declare ``op`` as a
``Literal`` over this set (or a subset of it).
"""


_FILTER_OPS: dict[FilterOp, str] = {
    "eq": "__eq__",
    "neq": "__ne__",
    "gt": "__gt__",
    "gte": "__ge__",
    "lt": "__lt__",
    "lte": "__le__",
    "contains": "contains",
    "starts_with": "startswith",
    "in": "in_",
}

_COMBINERS = {"and_": and_, "or_": or_}


def apply_filters(
    stmt: Select,
    node: BaseModel,
    model: type,
) -> Select:
    """Build WHERE clauses from a typed filter expression.

    Accepts a typed Pydantic filter model — either a single
    condition (with ``field``, ``op``, ``value``) or a combiner
    (with ``and_`` / ``or_`` lists of nested conditions). Models
    that match none of these shapes are treated as a no-op and
    the statement is returned unchanged.

    Args:
        stmt: The SQLAlchemy SELECT statement to filter.
        node: A Pydantic model representing the filter tree.
        model: The SQLAlchemy model class providing columns.

    Returns:
        The statement with WHERE clauses applied.

    """
    clause = _build_filter_clause(node, model)

    if clause is None:
        return stmt

    return stmt.where(clause)


def _build_filter_clause(
    node: BaseModel,
    model: type,
) -> ColumnElement[bool] | None:
    """Recursively build a SQLAlchemy clause from a filter node.

    Dispatches on node shape via attribute presence:

    - ``and_`` / ``or_`` attribute: combine child clauses.
    - ``field`` / ``op`` / ``value`` attributes: leaf condition.
    - Anything else: no-op, returns ``None``.

    Args:
        node: A Pydantic model representing a filter node.
        model: The SQLAlchemy model class.

    Returns:
        A SQLAlchemy clause element, or ``None`` for empty or
        shapeless nodes.

    """
    for attr, combiner in _COMBINERS.items():
        children = getattr(node, attr, None)
        if children is not None:
            return _combine(children, combiner, model)

    field_name = getattr(node, "field", None)
    if field_name is None:
        return None

    op: FilterOp = getattr(node, "op", "eq")
    value = getattr(node, "value", None)
    col = getattr(model, field_name)
    return getattr(col, _FILTER_OPS[op])(value)


def _combine(
    children: Sequence[BaseModel],
    combiner: Callable[..., ColumnElement[bool]],
    model: type,
) -> ColumnElement[bool] | None:
    """Build and combine clauses for a combiner node's children.

    Args:
        children: The child filter nodes.
        combiner: :func:`sqlalchemy.and_` or :func:`sqlalchemy.or_`.
        model: The SQLAlchemy model class.

    Returns:
        The combined clause, or ``None`` if every child built
        to ``None``.

    """
    built = (_build_filter_clause(child, model) for child in children)
    clauses = [clause for clause in built if clause is not None]
    return combiner(*clauses) if clauses else None
