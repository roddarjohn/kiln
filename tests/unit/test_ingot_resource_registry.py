"""Tests for :mod:`ingot.resource_registry`.

Discovery dispatch runs in pure Python — no DB needed.

Value-provider dispatch is exercised through a session stand-in
(:class:`_ExecuteCapture`) that records each SQL statement and
replies with canned scalar rows.  We assert *what* SQL the registry
asked for (compiled to a string with literal binds, the same
pattern :mod:`tests.unit.test_ingot_filters` uses) and *which* path
it dispatched on, rather than running queries against a database
that doesn't behave like the production target (Postgres).
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

from ingot.filter_values import FilterValuesRequest
from ingot.resource_registry import (
    Bool,
    Enum,
    FieldDiscoveryRequest,
    FieldRef,
    FilterDiscoveryRequest,
    FilterOperator,
    FreeText,
    LiteralField,
    Ref,
    ResourceEntry,
    ResourceRegistry,
)

if TYPE_CHECKING:
    from sqlalchemy.sql import Select


# -------------------------------------------------------------------
# Fixtures: an enum, a SQLAlchemy model, and a session stand-in.
# -------------------------------------------------------------------


class _Status(enum.StrEnum):
    DRAFT = enum.auto()
    PUBLISHED = enum.auto()
    ARCHIVED = enum.auto()


_Base = declarative_base()


class _Item(_Base):
    __tablename__ = "_filter_registry_test_items"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    sku = Column(String)


class _ExecuteCapture:
    """Minimal :class:`sqlalchemy.ext.asyncio.AsyncSession` stand-in.

    Records every executed statement, replies with a result whose
    ``scalars().all()`` returns the canned ``rows``.  Tests assert
    on :attr:`statements` (the compiled SQL) and on the registry's
    return value.
    """

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[Select[Any]] = []

    async def execute(self, stmt: Select[Any]) -> MagicMock:
        self.statements.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = self.rows
        result.all.return_value = self.rows
        return result


def _registry() -> ResourceRegistry:
    """One entry covering every field kind, with default search columns."""
    return ResourceRegistry(
        {
            "item": ResourceEntry(
                model=_Item,
                pk="id",
                fields=(
                    Enum("status", _Status),
                    FreeText("name"),
                    Ref("owner_id", target="owner"),
                    # ``self`` filter — codegen would translate
                    # ``values: "self"`` into ``Ref(target=<self_slug>)``.
                    Ref("id", target="item"),
                    LiteralField("count", type="int"),
                    Bool("active"),
                ),
                search_columns=("name", "sku"),
            ),
        }
    )


def _sql(stmt: Select[Any]) -> str:
    """Compile *stmt* against the Postgres dialect with literal binds.

    Postgres-specific operators (``ILIKE``) are dialect-erased to
    ``lower(...) LIKE lower(...)`` under the default dialect, so
    we pin to ``postgresql`` here — that's the production target,
    and it's the dialect we want to assert SQL shape against.
    """
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).replace("\n", " ")


# -------------------------------------------------------------------
# Discovery
# -------------------------------------------------------------------


def test_filter_discovery_full_payload() -> None:
    payload = _registry().filter_discovery(FilterDiscoveryRequest())
    slugs = {resource.resource for resource in payload.resources}
    assert slugs == {"item"}


def test_filter_discovery_subset_supports_search_flag() -> None:
    payload = _registry().filter_discovery(
        FilterDiscoveryRequest(resources=["item"])
    )
    assert len(payload.resources) == 1
    item_payload = payload.resources[0]
    assert item_payload.resource == "item"
    # ``supports_search`` replaces the old ``search: SearchDiscovery |
    # None`` — endpoint URL is implicit (``/_values/<slug>``).
    assert item_payload.supports_search is True
    fields = {entry.field: entry for entry in item_payload.filters}
    assert set(fields) == {
        "status",
        "name",
        "owner_id",
        "id",
        "count",
        "active",
    }


def test_supports_search_false_when_entry_lacks_search() -> None:
    registry = ResourceRegistry(
        {
            "owner": ResourceEntry(
                model=_Item,
                pk="id",
                fields=(FreeText("name"),),
            ),
        }
    )
    payload = registry.filter_discovery(FilterDiscoveryRequest())
    assert payload.resources[0].supports_search is False


def test_filter_discovery_empty_list_returns_no_resources() -> None:
    payload = _registry().filter_discovery(FilterDiscoveryRequest(resources=[]))
    assert payload.resources == []


def test_filter_discovery_unknown_resource_404() -> None:
    with pytest.raises(HTTPException) as ei:
        _registry().filter_discovery(FilterDiscoveryRequest(resources=["nope"]))

    assert ei.value.status_code == 404


def test_field_discovery_enum_includes_choices() -> None:
    response = _registry().field_discovery(
        FieldDiscoveryRequest(
            fields=[FieldRef(resource="item", field="status")],
        )
    )
    payload = response.fields[0]
    descriptor = payload.values
    assert descriptor.kind == "enum"
    labels = {choice.label for choice in descriptor.choices}
    assert labels == {"DRAFT", "PUBLISHED", "ARCHIVED"}


def test_field_discovery_self_renders_as_ref() -> None:
    """``values: "self"`` configs land as Ref descriptors targeting
    the resource's own slug."""
    response = _registry().field_discovery(
        FieldDiscoveryRequest(
            fields=[FieldRef(resource="item", field="id")],
        )
    )
    descriptor = response.fields[0].values
    assert descriptor.kind == "ref"
    assert descriptor.target == "item"


def test_field_discovery_operators_use_filter_operator_enum() -> None:
    response = _registry().field_discovery(
        FieldDiscoveryRequest(
            fields=[FieldRef(resource="item", field="status")],
        )
    )
    operators = response.fields[0].operators
    assert all(isinstance(op, FilterOperator) for op in operators)
    assert FilterOperator.EQ in operators
    assert FilterOperator.IN in operators


def test_field_discovery_preserves_request_order() -> None:
    response = _registry().field_discovery(
        FieldDiscoveryRequest(
            fields=[
                FieldRef(resource="item", field="name"),
                FieldRef(resource="item", field="status"),
            ],
        )
    )
    assert [entry.field for entry in response.fields] == ["name", "status"]


def test_field_discovery_unknown_field_404() -> None:
    with pytest.raises(HTTPException) as ei:
        _registry().field_discovery(
            FieldDiscoveryRequest(
                fields=[FieldRef(resource="item", field="nope")],
            )
        )

    assert ei.value.status_code == 404


def test_field_discovery_ref_carries_target_slug() -> None:
    """Ref descriptor exposes ``target`` for the FE to use as
    ``body.resource`` when calling ``POST /_values``.  The endpoint
    URL is implicit (``/_values``) so we don't dump it."""
    response = _registry().field_discovery(
        FieldDiscoveryRequest(
            fields=[FieldRef(resource="item", field="owner_id")],
        )
    )
    descriptor = response.fields[0].values
    assert descriptor.kind == "ref"
    assert descriptor.target == "owner"


# -------------------------------------------------------------------
# Values dispatch
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_field_values_without_q_return_empty_without_sql() -> None:
    """No ``q`` → empty page, no SQL fired (relevance scoring is
    meaningless without a query)."""
    db = _ExecuteCapture(rows=[])
    page = await _registry().values(
        resource="item",
        fields=["status"],
        request=FilterValuesRequest(),
        db=db,  # type: ignore[arg-type]
    )
    assert page.results == []
    assert db.statements == []


@pytest.mark.asyncio
async def test_enum_field_with_q_runs_trigram_on_labels() -> None:
    """Enum field + ``q`` → trigram match on the label VALUES table."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        fields=["status"],
        request=FilterValuesRequest(q="draft"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "VALUES" in sql
    assert "'DRAFT'" in sql
    assert "similarity" in sql.lower()
    # ``%`` is the trigram match operator (doubled by the PG dialect).
    assert "%% '%%draft%%'" in sql or "%%%%" in sql or "%% " in sql


@pytest.mark.asyncio
async def test_free_text_field_with_q_runs_trigram_on_column() -> None:
    """FreeText field + ``q`` → trigram on the source column, not ILIKE."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        fields=["name"],
        request=FilterValuesRequest(q="ap"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "ILIKE" not in sql.upper()
    assert "similarity" in sql.lower()
    assert "_filter_registry_test_items.name" in sql


@pytest.mark.asyncio
async def test_values_response_has_no_cursor() -> None:
    """Single-page only — :class:`ValuesPage` doesn't carry a cursor."""
    db = _ExecuteCapture(rows=[])
    page = await _registry().values(
        resource="item",
        fields=["name"],
        request=FilterValuesRequest(limit=2, q="ap"),
        db=db,  # type: ignore[arg-type]
    )
    assert "next_cursor" not in page.model_dump()


@pytest.mark.asyncio
async def test_empty_fields_unions_search_columns_with_q() -> None:
    """``fields=[]`` defaults to the entry's ``search_columns`` and
    runs the same trigram union pipeline as a populated list.
    Each search column appears in the compiled SQL and ``ILIKE``
    never does — the empty-fields path is purely trigram now."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        fields=[],
        request=FilterValuesRequest(q="ap"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "ILIKE" not in sql.upper()
    assert "similarity" in sql.lower()
    assert "_filter_registry_test_items.name" in sql
    assert "_filter_registry_test_items.sku" in sql


@pytest.mark.asyncio
async def test_empty_fields_without_search_columns_returns_empty() -> None:
    """A resource with no ``search_columns`` and no ``fields``
    short-circuits to an empty page — no SQL, no error."""
    registry = ResourceRegistry(
        {
            "item": ResourceEntry(
                model=_Item,
                pk="id",
                fields=(FreeText("name"),),
            ),
        }
    )
    db = _ExecuteCapture(rows=[])
    page = await registry.values(
        resource="item",
        fields=[],
        request=FilterValuesRequest(q="ap"),
        db=db,  # type: ignore[arg-type]
    )

    assert page.results == []
    assert db.statements == []


@pytest.mark.asyncio
async def test_self_ref_with_q_uses_target_search_col() -> None:
    """A self-ref Ref field is just a Ref whose target is the same
    resource — its trigram subquery scores against the target's
    first ``search_columns`` entry."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        fields=["id"],
        request=FilterValuesRequest(q="ap"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    # ``name`` is the first entry in the registry's search_columns.
    assert "_filter_registry_test_items.name" in sql
    assert "similarity" in sql.lower()


@pytest.mark.asyncio
async def test_ref_to_unsearched_resource_with_q_uses_pk_string_label() -> None:
    """Ref to a target without ``search_columns`` falls back to the
    target's stringified pk — trigram on ``cast(pk, String)`` so
    the union still composes."""
    registry = ResourceRegistry(
        {
            "item": ResourceEntry(
                model=_Item,
                pk="id",
                fields=(Ref("owner_id", target="owner"),),
            ),
            "owner": ResourceEntry(
                model=_Item,  # reuse for the test
                pk="id",
                fields=(),
                search_columns=(),  # crucial: no search columns
            ),
        }
    )
    db = _ExecuteCapture(rows=[])
    await registry.values(
        resource="item",
        fields=["owner_id"],
        request=FilterValuesRequest(q="42"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "CAST" in sql.upper()
    assert "similarity" in sql.lower()


@pytest.mark.asyncio
async def test_bool_and_literal_have_no_value_provider() -> None:
    db = _ExecuteCapture(rows=[])

    for field_name in ("active", "count"):
        with pytest.raises(HTTPException) as ei:
            await _registry().values(
                resource="item",
                fields=[field_name],
                request=FilterValuesRequest(),
                db=db,  # type: ignore[arg-type]
            )

        assert ei.value.status_code == 404
        assert db.statements == []


@pytest.mark.asyncio
async def test_unknown_field_404s_when_not_on_model() -> None:
    """Names that aren't a registered filter and aren't a column
    on the model raise 404, not silently empty results."""
    db = _ExecuteCapture(rows=[])

    with pytest.raises(HTTPException) as ei:
        await _registry().values(
            resource="item",
            fields=["nope"],
            request=FilterValuesRequest(q="x"),
            db=db,  # type: ignore[arg-type]
        )

    assert ei.value.status_code == 404
    assert db.statements == []
