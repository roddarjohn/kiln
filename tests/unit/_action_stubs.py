"""Stub action functions for testing introspection.

These are imported at generation time by
:func:`introspect_action_fn` during tests.
"""

# ruff: noqa: ARG001

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    # Reproduces the failure mode where a consumer guards a type
    # annotation behind ``TYPE_CHECKING`` -- the introspector should
    # surface this as a targeted ValueError naming the parameter.
    from typing import Any as UnresolvableType


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


async def collection_action_with_model_class(
    *,
    model_cls: type[StubMixin],
    db: AsyncSession,
    body: StubRequest,
) -> StubResponse:
    """Collection action that wants the resource's mapped class."""
    return StubResponse(ok=True)


async def action_with_typecheck_param(
    obj: StubModel,
    db: AsyncSession,
    helper: UnresolvableType,
) -> StubResponse:
    """Action with a parameter whose annotation can't be resolved
    at runtime (the import lives under ``if TYPE_CHECKING:``)."""
    return StubResponse(ok=True)


async def action_with_typecheck_return(
    obj: StubModel,
    db: AsyncSession,
) -> UnresolvableType:
    """Action whose return annotation can't be resolved at runtime."""
    return None  # pragma: no cover
