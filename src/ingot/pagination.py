"""Keyset and offset pagination helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession


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
        ``(modified_stmt, clamped_page_size)`` tuple.

    """
    page_size = min(page_size, max_page_size)
    if cursor is not None:
        col = getattr(model, cursor_field)
        stmt = stmt.where(col > cursor)
    return stmt.limit(page_size + 1), page_size


async def apply_offset_pagination(
    db: AsyncSession,
    stmt: Select,
    offset: int,
    limit: int,
    max_page_size: int,
) -> tuple[int, Sequence[Any]]:
    """Apply offset pagination and execute the query.

    Runs a ``COUNT(*)`` query for the total, then executes the
    statement with ``OFFSET`` / ``LIMIT``.

    Args:
        db: The async database session.
        stmt: The SQLAlchemy SELECT statement (without
            offset/limit applied).
        offset: Number of rows to skip.
        limit: Maximum rows to return.
        max_page_size: Hard ceiling on *limit*.

    Returns:
        ``(total, rows)`` tuple.

    """
    limit = min(limit, max_page_size)
    count_result = await db.execute(stmt.with_only_columns(func.count()))
    total = count_result.scalar_one()
    result = await db.execute(stmt.offset(offset).limit(limit))
    return total, list(result.scalars())
