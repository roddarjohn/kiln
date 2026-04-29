"""Tests for ``ingot.saved_views``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ingot.saved_views import (
    SavedViewCreate,
    SavedViewMixin,
    SavedViewUpdate,
    hydrate_view,
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def test_saved_view_create_requires_name():
    with pytest.raises(ValueError, match="name"):
        SavedViewCreate.model_validate({"payload": {}})


def test_saved_view_create_payload_defaults_empty():
    body = SavedViewCreate(name="hello")
    assert body.payload == {}


def test_saved_view_update_all_optional():
    body = SavedViewUpdate()
    assert body.name is None
    assert body.payload is None


def test_saved_view_update_partial():
    body = SavedViewUpdate.model_validate({"name": "new title"})
    assert body.name == "new title"
    assert body.payload is None


# ---------------------------------------------------------------------------
# Mixin column shape
# ---------------------------------------------------------------------------


class _Base(DeclarativeBase):
    pass


class _SavedView(_Base, SavedViewMixin):
    """Minimal subclass — used only to inspect the column metadata
    contributed by :class:`SavedViewMixin`."""

    __tablename__ = "saved_views_test"
    id: Mapped[str] = mapped_column(primary_key=True)


def test_mixin_columns_present():
    cols = {c.name for c in _SavedView.__table__.columns}
    assert {
        "id",
        "resource_type",
        "owner_id",
        "name",
        "payload",
        "created_at",
        "updated_at",
    } <= cols


def test_mixin_resource_type_indexed():
    col = _SavedView.__table__.columns["resource_type"]
    assert col.index is True


def test_mixin_owner_id_indexed():
    col = _SavedView.__table__.columns["owner_id"]
    assert col.index is True


# ---------------------------------------------------------------------------
# hydrate_view
# ---------------------------------------------------------------------------


@dataclass
class _StubView:
    """Minimal duck-typed stand-in for a real ``SavedView`` row."""

    id: str = "view-1"
    name: str = "Open orders, recent"
    resource_type: str = "order"
    owner_id: str = "user-1"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = datetime(2026, 4, 1, tzinfo=UTC)
    updated_at: datetime = datetime(2026, 4, 2, tzinfo=UTC)


def _resolver(items: list[dict[str, Any]], *, drop: int = 0) -> Any:
    """Build an async resolver that returns *items* + *drop* count."""

    async def fn(
        ids: list[Any],  # noqa: ARG001 -- recorded in items already
        db: Any,  # noqa: ARG001 -- not used by stub
        session: Any,  # noqa: ARG001 -- not used by stub
    ) -> tuple[list[dict[str, Any]], int]:
        return items, drop

    return fn


@pytest.mark.asyncio
async def test_hydrate_view_passes_non_link_entries_unchanged():
    view = _StubView(
        payload={
            "filters": [
                {
                    "field": "status",
                    "op": "eq",
                    "value": {"kind": "literal", "value": "open"},
                },
            ],
        },
    )
    out = await hydrate_view(view, ref_resolvers={}, db=None, session=None)

    assert out["payload"]["filters"] == [
        {
            "field": "status",
            "op": "eq",
            "value": {"kind": "literal", "value": "open"},
        },
    ]


@pytest.mark.asyncio
async def test_hydrate_view_resolves_ref_values_to_items():
    items = [
        {"type": "customer", "id": "c1", "name": "Acme"},
        {"type": "customer", "id": "c2", "name": "Beta"},
    ]
    resolvers = {"customer": _resolver(items)}
    view = _StubView(
        payload={
            "filters": [
                {
                    "field": "customer_id",
                    "op": "in",
                    "value": {
                        "kind": "ref",
                        "type": "customer",
                        "ids": ["c1", "c2"],
                    },
                },
            ],
        },
    )
    out = await hydrate_view(
        view, ref_resolvers=resolvers, db=None, session=None
    )
    entry = out["payload"]["filters"][0]
    assert entry["value"]["items"] == items
    assert entry["value"]["dropped"] == 0


@pytest.mark.asyncio
async def test_hydrate_view_resolves_self_kind_via_ref_resolvers():
    items = [{"type": "order", "id": "o1", "name": "#10003"}]
    resolvers = {"order": _resolver(items)}
    view = _StubView(
        payload={
            "filters": [
                {
                    "field": "id",
                    "op": "in",
                    "value": {
                        "kind": "self",
                        "type": "order",
                        "ids": ["o1"],
                    },
                },
            ],
        },
    )
    out = await hydrate_view(
        view, ref_resolvers=resolvers, db=None, session=None
    )
    entry = out["payload"]["filters"][0]
    assert entry["value"]["items"] == items
    assert entry["value"]["dropped"] == 0


@pytest.mark.asyncio
async def test_hydrate_view_drops_ids_when_no_resolver_for_type():
    view = _StubView(
        payload={
            "filters": [
                {
                    "field": "customer_id",
                    "op": "in",
                    "value": {
                        "kind": "ref",
                        "type": "customer",
                        "ids": ["c1", "c2", "c3"],
                    },
                },
            ],
        },
    )
    out = await hydrate_view(view, ref_resolvers={}, db=None, session=None)
    entry = out["payload"]["filters"][0]
    assert entry["value"]["items"] == []
    assert entry["value"]["dropped"] == 3


@pytest.mark.asyncio
async def test_hydrate_view_records_resolver_drop_count():
    items = [{"type": "customer", "id": "c1", "name": "Acme"}]
    resolvers = {"customer": _resolver(items, drop=1)}
    view = _StubView(
        payload={
            "filters": [
                {
                    "field": "customer_id",
                    "op": "in",
                    "value": {
                        "kind": "ref",
                        "type": "customer",
                        "ids": ["c1", "c2"],
                    },
                },
            ],
        },
    )
    out = await hydrate_view(
        view, ref_resolvers=resolvers, db=None, session=None
    )
    entry = out["payload"]["filters"][0]
    assert entry["value"]["items"] == items
    assert entry["value"]["dropped"] == 1


@pytest.mark.asyncio
async def test_hydrate_view_handles_empty_payload():
    view = _StubView(payload={})
    out = await hydrate_view(view, ref_resolvers={}, db=None, session=None)
    assert out["payload"] == {"filters": []}
    assert out["id"] == "view-1"
    assert out["resource_type"] == "order"
    assert out["created_at"] == "2026-04-01T00:00:00+00:00"
