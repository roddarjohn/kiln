"""Inventory actions — stubs for playground testing."""

from __future__ import annotations


async def ping_product(
    pk: object,
    *,
    db: object,
) -> dict:
    """Ping a product."""
    return {"status": "pong"}


async def ping_event_log(
    pk: object,
    *,
    db: object,
) -> dict:
    """Ping an event log."""
    return {"status": "pong"}
