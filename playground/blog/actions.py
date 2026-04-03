"""Blog actions — stubs for playground testing."""

from __future__ import annotations


async def publish_article(
    pk: object,
    *,
    db: object,
    notify_subscribers: bool = False,
) -> dict:
    """Publish an article."""
    return {"status": "published", "notified": notify_subscribers}


async def archive_article(
    pk: object,
    *,
    db: object,
    reason: str = "",
) -> dict:
    """Archive an article."""
    return {"status": "archived", "reason": reason}
