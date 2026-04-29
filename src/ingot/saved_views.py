"""Per-user saved views: model + payload schemas.

A *saved view* is a named filter+sort state stored on behalf of a
single user.  The shared ``SavedView`` SQLAlchemy table carries
all resources' views with a ``resource_type`` discriminator
column; the codegen emits one CRUD surface per opted-in resource
that scopes both reads and writes to ``resource_type=<slug>`` and
``owner_id=<session.user_id>``.

Stored payloads keep raw filter values only — including raw ids
for ``ref`` values.  Label hydration for those ids is the FE's
responsibility in v1: it calls the target resource's
``POST /_values`` endpoint to resolve them.  A future iteration
can add BE-side hydration once the per-id lookup story exists
(today's ``_values`` endpoints search by ``q``, not by ``id``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SavedViewBase(DeclarativeBase):
    """Declarative base for the shared ``saved_views`` table.

    Lives alongside the consumer's own models in their database.
    Consumers running migrations on this table should target this
    base's metadata explicitly.
    """


def _now_utc() -> datetime:
    """Timezone-aware ``utcnow``; stored as a tz-aware column."""
    return datetime.now(UTC)


class SavedView(SavedViewBase):
    """One saved view record for one user on one resource type."""

    __tablename__ = "saved_views"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, onupdate=_now_utc
    )


class SavedViewCreate(BaseModel):
    """Request body for ``POST /views``.

    ``payload`` is the raw filter+sort spec — same shape as
    :class:`SavedViewUpdate`'s, but ``name`` is required.
    """

    name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SavedViewUpdate(BaseModel):
    """Request body for ``PATCH /views/{id}``.

    Both fields optional; missing fields leave the stored value
    untouched.
    """

    name: str | None = None
    payload: dict[str, Any] | None = None


def dump_view(view: SavedView) -> dict[str, Any]:
    """Serialize a :class:`SavedView` to the JSON dump shape.

    The shape is intentionally flat and FE-agnostic; ref filter
    values stay as ``{kind: "ref", type, ids}`` and the FE
    resolves labels through the target resource's ``_values``
    endpoint.
    """
    return {
        "id": view.id,
        "name": view.name,
        "resource_type": view.resource_type,
        "owner_id": view.owner_id,
        "payload": dict(view.payload or {}),
        "created_at": view.created_at.isoformat(),
        "updated_at": view.updated_at.isoformat(),
    }
