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
from dataclasses import dataclass
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
    SearchSpec,
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
    # Stand-in for a pgcraft-generated tsvector column.  The real
    # type would be ``TSVECTOR`` from sqlalchemy.dialects.postgresql,
    # but this test only compiles SQL — it never executes — so the
    # column type doesn't matter, only its presence.
    search_vector = Column(String)


@dataclass(frozen=True)
class _Link:
    """Stand-in for the consumer's link schema; mirrors
    :meth:`pydantic.BaseModel.model_dump` so the registry's
    ``hasattr(link, "model_dump")`` branch fires."""

    type: str
    id: int
    name: str

    def model_dump(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "name": self.name}


async def _link_item(obj: _Item, _session: Any) -> _Link:
    return _Link(type="item", id=obj.id, name=obj.name)


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
    """One entry covering every field kind, with a SearchSpec."""
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
                search=SearchSpec(columns=("name", "sku"), link=_link_item),
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
                search=None,
            ),
        }
    )
    payload = registry.filter_discovery(FilterDiscoveryRequest())
    assert payload.resources[0].supports_search is False


def test_filter_discovery_empty_list_returns_no_resources() -> None:
    payload = _registry().filter_discovery(
        FilterDiscoveryRequest(resources=[])
    )
    assert payload.resources == []


def test_filter_discovery_unknown_resource_404() -> None:
    with pytest.raises(HTTPException) as ei:
        _registry().filter_discovery(
            FilterDiscoveryRequest(resources=["nope"])
        )
    assert ei.value.status_code == 404


def test_field_discovery_enum_includes_choices_and_endpoint() -> None:
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
    assert descriptor.endpoint == "/_values/status"


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
    assert descriptor.endpoint == "/_values/item"


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


def test_field_discovery_ref_endpoint_always_set() -> None:
    """Ref descriptor always exposes an endpoint — the registry
    serves a search when configured and falls back to a pk-ordered
    page otherwise, so the FE always has a URL to call."""
    response = _registry().field_discovery(
        FieldDiscoveryRequest(
            fields=[FieldRef(resource="item", field="owner_id")],
        )
    )
    descriptor = response.fields[0].values
    assert descriptor.kind == "ref"
    assert descriptor.target == "owner"
    assert descriptor.endpoint == "/_values/owner"


# -------------------------------------------------------------------
# Values dispatch
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enum_values_run_through_postgres_values_clause() -> None:
    """Enum values now go through the same SQL pipeline as everything
    else — a ``VALUES (...)`` clause is built and queried."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        field="status",
        request=FilterValuesRequest(),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "VALUES" in sql
    # SQL only carries labels — the enum values are looked up
    # Python-side from the enum class (no point round-tripping
    # them through the DB).
    assert "'DRAFT'" in sql
    assert "'PUBLISHED'" in sql
    assert "'ARCHIVED'" in sql


@pytest.mark.asyncio
async def test_enum_values_with_q_filters_via_ilike() -> None:
    """A query narrows the VALUES table via ILIKE on label."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        field="status",
        request=FilterValuesRequest(q="draft"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    # PG dialect doubles ``%`` literals for psycopg parameter
    # substitution.
    assert "ILIKE '%%draft%%'" in sql


@pytest.mark.asyncio
async def test_free_text_values_no_q_uses_keyset_pagination() -> None:
    """No ``q`` → keyset on the column, ``LIMIT n+1``, no ILIKE."""
    db = _ExecuteCapture(rows=["apple", "apricot"])
    await _registry().values(
        resource="item",
        field="name",
        request=FilterValuesRequest(limit=2),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "SELECT DISTINCT" in sql
    assert "_filter_registry_test_items.name" in sql
    assert "LIMIT 3" in sql
    assert "ILIKE" not in sql.upper()


@pytest.mark.asyncio
async def test_free_text_values_with_q_filters_no_bucket() -> None:
    """A query just adds the ILIKE WHERE; no bucket-relevance CASE.

    Single-column ordering keeps the SQL simple — no ``CASE WHEN``
    relevance bucket, no compound keyset.
    """
    db = _ExecuteCapture(rows=["apple", "apricot"])
    await _registry().values(
        resource="item",
        field="name",
        request=FilterValuesRequest(q="ap"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "ILIKE '%%ap%%'" in sql
    assert "CASE WHEN" not in sql.upper()


@pytest.mark.asyncio
async def test_free_text_values_keyset_cursor_round_trip() -> None:
    """Keyset cursor decodes back into a ``WHERE col > prev`` clause."""
    db = _ExecuteCapture(rows=["apple", "apricot", "banana"])
    page1 = await _registry().values(
        resource="item",
        field="name",
        request=FilterValuesRequest(limit=2),
        db=db,  # type: ignore[arg-type]
    )

    # Cursor is just the previous-key value as a string — no
    # prefix tag, since each endpoint already knows what its
    # ordering key is.
    assert page1.next_cursor == "apricot"

    db2 = _ExecuteCapture(rows=["banana"])
    await _registry().values(
        resource="item",
        field="name",
        request=FilterValuesRequest(limit=2, cursor=page1.next_cursor),
        db=db2,  # type: ignore[arg-type]
    )

    sql2 = _sql(db2.statements[0])
    assert "> 'apricot'" in sql2


@pytest.mark.asyncio
async def test_search_resource_with_tsvector_column_uses_ts_rank() -> None:
    """SearchSpec.vector_column switches the SQL to tsvector mode."""
    registry = ResourceRegistry(
        {
            "item": ResourceEntry(
                model=_Item,
                pk="id",
                fields=(),
                search=SearchSpec(
                    columns=("name",),
                    link=_link_item,
                    vector_column="search_vector",
                ),
            ),
        }
    )
    db = _ExecuteCapture(rows=[])
    await registry.values(
        resource="item",
        field=None,
        request=FilterValuesRequest(q="apple"),
        db=db,  # type: ignore[arg-type]
    )

    sql = str(db.statements[0].compile(dialect=postgresql.dialect())).replace(
        "\n", " "
    )

    assert "@@" in sql
    assert "websearch_to_tsquery" in sql
    assert "ts_rank" in sql
    assert "ILIKE" not in sql.upper()


@pytest.mark.asyncio
async def test_search_resource_without_vector_column_keeps_ilike() -> None:
    """Without vector_column the ILIKE fallback fires, keyset on pk."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        field=None,
        request=FilterValuesRequest(q="apple"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "@@" not in sql
    assert "ts_rank" not in sql
    assert "ILIKE" in sql.upper()
    # Single-column ordering: pk, no relevance bucket.
    assert "CASE WHEN" not in sql.upper()


@pytest.mark.asyncio
async def test_search_resource_with_q_or_ilikes_every_search_column() -> None:
    """Resource-level search ORs ILIKE over every column in SearchSpec."""
    db = _ExecuteCapture(rows=[])
    await _registry().values(
        resource="item",
        field=None,
        request=FilterValuesRequest(q="ap"),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "ILIKE '%%ap%%'" in sql
    assert " OR " in sql.upper()


@pytest.mark.asyncio
async def test_self_ref_field_dispatches_to_resource_search() -> None:
    """SelfRef field → same SQL as the resource-level search."""
    db_self = _ExecuteCapture(rows=[])
    db_root = _ExecuteCapture(rows=[])
    req = FilterValuesRequest(q="ap")

    await _registry().values(
        resource="item",
        field="id",
        request=req,
        db=db_self,  # type: ignore[arg-type]
    )
    await _registry().values(
        resource="item",
        field=None,
        request=req,
        db=db_root,  # type: ignore[arg-type]
    )

    assert _sql(db_self.statements[0]) == _sql(db_root.statements[0])


@pytest.mark.asyncio
async def test_ref_to_unsearched_resource_falls_back_to_pk_only() -> None:
    """Ref to a target without a SearchSpec returns the first N rows
    by pk — no 404, no search query.  Gives the FE something to
    render even when the consumer hasn't wired a real search."""
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
                search=None,  # crucial: no SearchSpec
            ),
        }
    )
    db = _ExecuteCapture(rows=[])
    await registry.values(
        resource="item",
        field="owner_id",
        request=FilterValuesRequest(),
        db=db,  # type: ignore[arg-type]
    )

    sql = _sql(db.statements[0])
    assert "ORDER BY" in sql.upper()
    assert "_filter_registry_test_items.id" in sql
    # No q-filtering — fallback ignores it entirely.
    assert "ILIKE" not in sql.upper()


@pytest.mark.asyncio
async def test_bool_and_literal_have_no_value_provider() -> None:
    db = _ExecuteCapture(rows=[])
    for field in ("active", "count"):
        with pytest.raises(HTTPException) as ei:
            await _registry().values(
                resource="item",
                field=field,
                request=FilterValuesRequest(),
                db=db,  # type: ignore[arg-type]
            )
        assert ei.value.status_code == 404
        assert db.statements == []
