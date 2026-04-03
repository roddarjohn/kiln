"""Stub action functions for testing introspection.

These are imported at generation time by
:func:`introspect_action_fn` during tests.
"""

# ruff: noqa: ARG001

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002


class StubModel:
    """Fake SQLAlchemy model for testing."""


class StubRequest(BaseModel):
    """Fake request body."""

    value: str


class StubResponse(BaseModel):
    """Fake response."""

    ok: bool


async def object_action_with_body(
    obj: StubModel,
    db: AsyncSession,
    body: StubRequest,
) -> StubResponse:
    """Object action with a request body."""
    return StubResponse(ok=True)


async def object_action_no_body(
    obj: StubModel,
    db: AsyncSession,
) -> StubResponse:
    """Object action without a request body."""
    return StubResponse(ok=True)


async def collection_action_with_body(
    db: AsyncSession,
    body: StubRequest,
) -> StubResponse:
    """Collection action with a request body."""
    return StubResponse(ok=True)


async def collection_action_no_body(
    db: AsyncSession,
) -> StubResponse:
    """Collection action without a request body."""
    return StubResponse(ok=True)


async def action_no_return(
    obj: StubModel,
    db: AsyncSession,
) -> dict:  # type: ignore[type-arg]
    """Action with non-BaseModel return — should fail."""
    return {}


async def action_no_annotations(obj, db):
    """Action with no annotations — should fail."""
    return {}
