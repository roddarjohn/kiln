"""Inventory queries — stubs for playground testing."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from inventory.models import Product


class StockLevelsRequest(BaseModel):
    """Request body for the stock levels query."""

    start_date: date | None = None
    end_date: date | None = None


class StockLevelsResponse(BaseModel):
    """Response from the stock levels query."""

    status: str
    start_date: str | None
    end_date: str | None


async def stock_levels_by_date(
    product: Product,
    db: AsyncSession,
    body: StockLevelsRequest,
) -> StockLevelsResponse:
    """Return stock levels for a product in a date range."""
    return StockLevelsResponse(
        status="ok",
        start_date=(
            str(body.start_date)
            if body.start_date
            else None
        ),
        end_date=(
            str(body.end_date) if body.end_date else None
        ),
    )
