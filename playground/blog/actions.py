"""Blog actions — stubs for playground testing."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from blog.models.article import Article


class PublishRequest(BaseModel):
    """Request body for the publish action."""

    notify_subscribers: bool = False


class PublishResponse(BaseModel):
    """Response from the publish action."""

    status: str
    notified: bool


class ArchiveRequest(BaseModel):
    """Request body for the archive action."""

    reason: str = ""


class ArchiveResponse(BaseModel):
    """Response from the archive action."""

    status: str
    reason: str


async def publish_article(
    article: Article,
    db: AsyncSession,
    body: PublishRequest,
) -> PublishResponse:
    """Publish an article."""
    return PublishResponse(
        status="published",
        notified=body.notify_subscribers,
    )


async def archive_article(
    article: Article,
    db: AsyncSession,
    body: ArchiveRequest,
) -> ArchiveResponse:
    """Archive an article."""
    return ArchiveResponse(
        status="archived",
        reason=body.reason,
    )
