"""ORDER BY application from typed Pydantic sort clauses."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel
    from sqlalchemy import Select


def apply_ordering(
    stmt: Select,
    sort_clauses: Sequence[BaseModel] | None,
    model: type,
    default_field: str,
    default_dir: str = "asc",
) -> Select:
    """Apply one or more sort clauses to a SELECT statement.

    Each clause is a Pydantic model with ``field`` (an enum
    whose ``.value`` is the column name) and ``dir``
    (``"asc"`` or ``"desc"``).

    When *sort_clauses* is ``None`` or empty, the default
    field and direction are used.

    Args:
        stmt: The SQLAlchemy SELECT statement to sort.
        sort_clauses: List of sort clause models, or ``None``.
        model: The SQLAlchemy model class providing columns.
        default_field: Column name to sort by when no clauses
            are provided.
        default_dir: Direction for the default sort
            (``"asc"`` or ``"desc"``).

    Returns:
        The statement with ORDER BY applied.

    """
    if not sort_clauses:
        col = getattr(model, default_field)
        if default_dir == "desc":
            return stmt.order_by(col.desc())
        return stmt.order_by(col.asc())

    for clause in sort_clauses:
        field_enum = getattr(clause, "field")  # noqa: B009
        col = getattr(model, field_enum.value)
        direction = getattr(clause, "dir", "asc")
        if direction == "desc":
            stmt = stmt.order_by(col.desc())
        else:
            stmt = stmt.order_by(col.asc())
    return stmt
