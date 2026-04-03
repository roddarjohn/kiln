"""Inventory queries — stubs for playground testing."""

from __future__ import annotations

from datetime import date


async def stock_levels_by_date(
    pk: object,
    *,
    db: object,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """Return stock levels for a product in a date range."""
    return {
        "status": "ok",
        "start_date": str(start_date),
        "end_date": str(end_date),
    }
