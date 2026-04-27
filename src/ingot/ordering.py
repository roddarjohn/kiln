"""ORDER BY application from typed Pydantic sort clauses."""

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel
    from sqlalchemy import Select


SortDirection = Literal["asc", "desc"]
"""Direction keys accepted by a sort clause's ``dir`` field.

Callers' Pydantic sort-clause models should declare ``dir`` as
a ``Literal`` over this set (or use this alias directly).
"""


def apply_ordering(
    stmt: Select,
    sort_clauses: Sequence[BaseModel] | None,
    model: type,
    default_field: str,
    default_dir: SortDirection = "asc",
) -> Select:
    """Apply one or more sort clauses to a SELECT statement.

    Each clause is a Pydantic model with ``field`` (an enum
    whose ``.value`` is the column name) and ``dir``
    (:data:`SortDirection`).

    When *sort_clauses* is ``None`` or empty, the default
    field and direction are used.

    Args:
        stmt: The SQLAlchemy SELECT statement to sort.
        sort_clauses: List of sort clause models, or ``None``.
        model: The SQLAlchemy model class providing columns.
        default_field: Column name to sort by when no clauses
            are provided.
        default_dir: Direction for the default sort.

    Returns:
        The statement with ORDER BY applied.

    """
    if not sort_clauses:
        default_col = getattr(model, default_field)
        return stmt.order_by(_sort_expr(default_col, default_dir))

    for clause in sort_clauses:
        field_enum = getattr(clause, "field", None)

        if field_enum is None:
            continue

        direction: SortDirection = getattr(clause, "dir", "asc")
        col = getattr(model, field_enum.value)
        stmt = stmt.order_by(_sort_expr(col, direction))

    return stmt


def _sort_expr(col: Any, direction: SortDirection) -> Any:
    """Wrap *col* in the given sort direction.

    Args:
        col: A SQLAlchemy column.
        direction: ``"asc"`` or ``"desc"``.

    Returns:
        ``col.asc()`` or ``col.desc()``.

    """
    return col.desc() if direction == "desc" else col.asc()
