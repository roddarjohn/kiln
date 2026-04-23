"""Filter-clause construction for typed Pydantic filter trees."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, or_

if TYPE_CHECKING:
    from pydantic import BaseModel
    from sqlalchemy import Select

_FILTER_OPS: dict[str, str] = {
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


def apply_filters(
    stmt: Select,
    node: BaseModel,
    model: type,
) -> Select:
    """Build WHERE clauses from a typed filter expression.

    Accepts a typed Pydantic filter model — either a single
    ``FilterCondition`` (with ``field``, ``op``, ``value``)
    or a ``FilterExpression`` (with ``and_`` / ``or_`` lists
    of nested conditions).

    Field names and operators are validated by the Pydantic
    model's ``Literal`` types before this function is called.

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
) -> Any:
    """Recursively build a SQLAlchemy clause from a filter node.

    Args:
        node: A Pydantic model representing a filter node.
        model: The SQLAlchemy model class.

    Returns:
        A SQLAlchemy clause element, or ``None`` for empty
        combiner lists.

    """
    and_nodes = getattr(node, "and_", None)
    if and_nodes is not None:
        children = [_build_filter_clause(child, model) for child in and_nodes]
        children = [c for c in children if c is not None]
        if not children:
            return None
        return and_(*children)

    or_nodes = getattr(node, "or_", None)
    if or_nodes is not None:
        children = [_build_filter_clause(child, model) for child in or_nodes]
        children = [c for c in children if c is not None]
        if not children:
            return None
        return or_(*children)

    field_name = getattr(node, "field", None)
    if field_name is None:
        return None

    op = getattr(node, "op", "eq")
    value = getattr(node, "value", None)
    col = getattr(model, field_name)
    op_method = _FILTER_OPS[op]
    return getattr(col, op_method)(value)
