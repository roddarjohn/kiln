"""Inventory actions — stubs for playground testing."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from inventory.models import EventLog, Product


class PingResponse(BaseModel):
    """Response from a ping action."""

    status: str


async def ping_product(
    product: Product,
    db: AsyncSession,
) -> PingResponse:
    """Ping a product."""
    return PingResponse(status="pong")


async def ping_event_log(
    event_log: EventLog,
    db: AsyncSession,
) -> PingResponse:
    """Ping an event log."""
    return PingResponse(status="pong")
