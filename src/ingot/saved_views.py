"""Per-user saved views: mixin + payload schemas + hydration.

A *saved view* is a named filter+sort state stored on behalf of a
single user.  Mirrors the :class:`ingot.files.FileMixin` idiom:
the consumer subclasses :class:`SavedViewMixin` on their own
``DeclarativeBase`` and defines a normal kiln resource pointing
at it.

A single mixed-in model serves every opted-in resource;
``resource_type`` discriminates rows so the codegen-generated
CRUD scopes reads and writes per resource.

Stored payloads keep raw filter values, including raw ids on
``ref`` values.  Read paths run those ids through
:func:`hydrate_view`, which looks each ref type up in the per-app
``REF_RESOLVERS`` mapping (also generated alongside ``LINKS``)
and returns hydrated ``items`` with a ``dropped`` count for stale
or invisible refs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _now_utc() -> datetime:
    """Timezone-aware ``utcnow``; stored as a tz-aware column."""
    return datetime.now(UTC)


class SavedViewMixin:
    """SQLAlchemy mixin supplying the columns of a saved-view row.

    Subclass on a ``DeclarativeBase``-derived class:

    .. code-block:: python

        from ingot.saved_views import SavedViewMixin

        class SavedView(Base, SavedViewMixin):
            __tablename__ = "saved_views"

    Then point each opted-in resource at the model:

    .. code-block:: jsonnet

        {
          model: "myapp.models.Product",
          saved_views: { model: "myapp.models.SavedView" },
          link: { kind: "id_name", name: "name" },
          // ...
        }

    The mixin owns no primary key — declare ``id`` on the consumer
    class (typically a UUID column) so the consumer's PK
    convention wins.  Indexes on ``resource_type`` and ``owner_id``
    are recommended; both columns drive every read filter.
    """

    if TYPE_CHECKING:
        # Type-only — concrete column lives on the consumer class.
        # Kept here so generated code can rely on ``view.id`` being
        # typed without committing to a column the consumer may
        # name differently.
        id: Mapped[Any]

    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    """Slug of the parent resource (lowercase model class name).
    Drives the ``WHERE resource_type = ...`` clause every saved-view
    read / write inserts so views never bleed across resources."""

    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    """Stringified user id.  Saved views are per-user; the
    generated routes filter by ``owner_id == str(session.<attr>)``
    where ``<attr>`` is :attr:`~be.config.schema.AuthConfig.user_id_attr`."""

    name: Mapped[str] = mapped_column(String(255))
    """Caller-supplied display name."""

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
    )
    """Raw filter+sort spec.  Ref values store ids only; hydration
    happens at read time via :func:`hydrate_view`."""

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


RefResolver = (
    "Callable[[list[Any], AsyncSession, Any],"
    " Awaitable[tuple[list[dict[str, Any]], int]]]"
)
"""Type alias for the per-resource ref resolvers stored in the
generated ``REF_RESOLVERS`` mapping.  Each resolver fetches rows
by id, runs them through the resource's link builder, and returns
``(items, dropped)``.  Kept as a string to avoid forcing
SQLAlchemy / typing imports at module load."""


async def hydrate_view(
    view: SavedViewMixin,
    ref_resolvers: dict[str, Any],
    db: AsyncSession,
    session: Any,
) -> dict[str, Any]:
    """Return the dump-format payload for one saved view.

    Walks each entry in ``view.payload["filters"]``; for entries
    with ``value.kind == "ref"``, looks up the target type in
    *ref_resolvers* and replaces ``ids`` with hydrated ``items``.
    Stale or invisible rows bump ``dropped`` rather than erroring
    so the dump never throws because of dangling refs.

    All other shapes pass through unchanged so the FE sees a
    single uniform structure.
    """
    payload = dict(view.payload or {})
    raw_filters = list(payload.get("filters") or [])
    hydrated_filters: list[dict[str, Any]] = [
        await _hydrate_entry(entry, ref_resolvers, db, session)
        for entry in raw_filters
    ]
    payload["filters"] = hydrated_filters
    return {
        "id": str(view.id) if view.id is not None else None,
        "name": view.name,
        "resource_type": view.resource_type,
        "owner_id": view.owner_id,
        "payload": payload,
        "created_at": view.created_at.isoformat(),
        "updated_at": view.updated_at.isoformat(),
    }


async def _hydrate_entry(
    entry: dict[str, Any],
    ref_resolvers: dict[str, Any],
    db: AsyncSession,
    session: Any,
) -> dict[str, Any]:
    """Hydrate one filter entry, leaving non-link values untouched.

    Both ``ref`` (FK to another resource) and ``self`` (PK of
    this resource) values dump as link schemas, so they share
    the same hydration path: look the type up in *ref_resolvers*
    and replace ``ids`` with hydrated ``items``.
    """
    value = entry.get("value")

    if not isinstance(value, dict):
        return entry

    if value.get("kind") not in {"ref", "self"}:
        return entry

    ref_type = value.get("type")
    resolver = (
        ref_resolvers.get(ref_type) if isinstance(ref_type, str) else None
    )
    ids: list[Any] = list(value.get("ids") or [])

    if resolver is None or not ids:
        return {
            **entry,
            "value": {**value, "items": [], "dropped": len(ids)},
        }

    items, dropped = await resolver(list(ids), db, session)
    return {
        **entry,
        "value": {**value, "items": items, "dropped": dropped},
    }
