"""Keyset and offset pagination helpers."""

from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import and_, func, or_, select

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import ColumnElement, Select


SortDirection = Literal["asc", "desc"]
"""Per-column ordering direction for compound keyset cursors."""


def apply_keyset_pagination(
    stmt: Select,
    model: type,
    cursor: Any,
    cursor_field: str,
    page_size: int,
    max_page_size: int,
) -> tuple[Select, int]:
    """Apply keyset (cursor-based) pagination to a SELECT.

    Adds a ``WHERE cursor_field > cursor`` clause when a
    cursor is provided, clamps *page_size*, and adds
    ``LIMIT page_size + 1`` (the extra row detects whether
    more results exist).

    Args:
        stmt: The SQLAlchemy SELECT statement.
        model: The SQLAlchemy model class providing columns.
        cursor: The cursor value (already cast to the correct
            type), or ``None``.
        cursor_field: Name of the cursor column.
        page_size: Requested page size.
        max_page_size: Maximum allowed page size.

    Returns:
        ``(paginated_stmt, effective_page_size)`` tuple. The
        caller is responsible for executing *paginated_stmt*.

    """
    effective_page_size = min(page_size, max_page_size)

    if cursor is not None:
        cursor_col = getattr(model, cursor_field)
        stmt = stmt.where(cursor_col > cursor)

    return stmt.limit(effective_page_size + 1), effective_page_size


def apply_compound_keyset_pagination(
    stmt: Select,
    columns: Sequence[tuple[ColumnElement[Any], SortDirection]],
    cursor: Sequence[Any] | None,
    page_size: int,
    max_page_size: int,
) -> tuple[Select, int]:
    """Apply N-column keyset pagination with per-column directions.

    Resumes after a cursor of N values matching N ``(column,
    direction)`` pairs.  Required whenever the lead ordering column
    isn't unique — the trailing column(s) break ties so pagination
    stays stable.

    The resume predicate is the standard lex-comparison expansion::

        (c1 > p1)
        OR (c1 == p1 AND c2 > p2)
        OR (c1 == p1 AND c2 == p2 AND c3 > p3)
        ...

    with ``>`` flipped to ``<`` on any column whose direction is
    ``"desc"``.  This form works for any mix of ASC/DESC; the
    ``tuple_()`` row-constructor shorthand only handles all-ASC.

    Args:
        stmt: SELECT to extend.
        columns: Ordered ``(expression, direction)`` pairs matching
            the statement's ORDER BY.
        cursor: One previous value per column, in the same order;
            ``None`` skips the WHERE and returns just the LIMIT.
        page_size: Requested page size.
        max_page_size: Hard ceiling.

    Returns:
        ``(paginated_stmt, effective_page_size)``.  The statement
        carries ``LIMIT effective_page_size + 1`` (over-fetch by
        one so the caller can detect "more pages exist").

    """
    effective_page_size = min(page_size, max_page_size)

    if cursor is not None:
        if len(cursor) != len(columns):
            msg = (
                f"Cursor has {len(cursor)} values but {len(columns)} "
                f"ordering columns were declared."
            )
            raise ValueError(msg)

        stmt = stmt.where(_compound_resume(columns, cursor))

    return stmt.limit(effective_page_size + 1), effective_page_size


def _compound_resume(
    columns: Sequence[tuple[ColumnElement[Any], SortDirection]],
    cursor: Sequence[Any],
) -> ColumnElement[bool]:
    """Build the lex-comparison WHERE clause for compound keyset."""
    clauses: list[ColumnElement[bool]] = []

    for ith in range(len(columns)):
        equality_prefix = [
            columns[index][0] == cursor[index] for index in range(ith)
        ]
        column, direction = columns[ith]
        previous = cursor[ith]
        strict = column > previous if direction == "asc" else column < previous
        clauses.append(and_(*equality_prefix, strict))

    return or_(*clauses)


def apply_offset_pagination(
    stmt: Select,
    offset: int,
    limit: int,
    max_page_size: int,
) -> tuple[Select, Select, int]:
    """Apply offset pagination to a SELECT.

    Clamps *limit* to *max_page_size*, applies ``OFFSET`` /
    ``LIMIT``, and builds a companion ``COUNT(*)`` statement so
    the caller can fetch the total alongside the page.

    Args:
        stmt: The SQLAlchemy SELECT statement (without
            offset/limit applied).
        offset: Number of rows to skip.
        limit: Requested page size.
        max_page_size: Hard ceiling on *limit*.

    Returns:
        ``(paginated_stmt, count_stmt, effective_limit)`` tuple.
        The caller is responsible for executing both statements.

    """
    effective_limit = min(limit, max_page_size)
    paginated_stmt = stmt.offset(offset).limit(effective_limit)
    # Wrap in a subquery so we keep WHERE/ORDER/JOINs from *stmt*
    # while replacing columns with count(*).  ``with_only_columns``
    # alone drops the implicit FROM that ``select(Model)`` derives
    # from the model column, leaving a FROM-less SELECT count(*).
    count_stmt = select(func.count()).select_from(stmt.subquery())
    return paginated_stmt, count_stmt, effective_limit
