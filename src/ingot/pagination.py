"""Keyset and offset pagination helpers."""

from typing import TYPE_CHECKING, Any

from sqlalchemy import func

if TYPE_CHECKING:
    from sqlalchemy import Select


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
    count_stmt = stmt.with_only_columns(func.count())
    return paginated_stmt, count_stmt, effective_limit
