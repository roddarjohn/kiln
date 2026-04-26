"""Stub action functions for testing introspection.

These are imported at generation time by
:func:`introspect_action_fn` during tests.
"""

# ruff: noqa: ARG001

from __future__ import annotations

from pydantic import BaseModel


class StubModel:
    """Fake SQLAlchemy model for testing."""


class AsyncSession:
    """Fake AsyncSession so stubs don't need sqlalchemy."""


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
) -> dict:
    """Action with non-BaseModel return — should fail."""
    return {}


async def action_no_annotations(obj, db):
    """Action with no annotations — should fail."""
    return {}


async def action_two_bodies(
    obj: StubModel,
    db: AsyncSession,
    body: StubRequest,
    extra: StubRequest,
) -> StubResponse:
    """Action with two BaseModel params — should fail."""
    return StubResponse(ok=True)


class StubMixin:
    """Stand-in for a mixin class shared across resources."""


class StubModelWithMixin(StubMixin):
    """Concrete model that extends the mixin."""


async def object_action_supertype(
    obj: StubMixin,
    db: AsyncSession,
) -> StubResponse:
    """Object action whose model param is a supertype of the model."""
    return StubResponse(ok=True)


async def object_action_object_typed(
    obj: object,
    db: AsyncSession,
) -> StubResponse:
    """Object action whose first param is typed ``object``."""
    return StubResponse(ok=True)


async def object_action_returns_none(
    obj: StubModel,
    db: AsyncSession,
) -> None:
    """Object action with no body -- 204 No Content."""


async def collection_action_returns_none(
    db: AsyncSession,
) -> None:
    """Collection action with no body -- 204 No Content."""
