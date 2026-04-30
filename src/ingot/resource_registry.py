"""Project-wide resource registry: discovery + value-provider engine.

Codegen emits one :class:`ResourceRegistry` per project, populated
declaratively with one :class:`ResourceEntry` per resource.  Three
generated route handlers delegate to it:

* ``POST /_filters`` — :meth:`ResourceRegistry.filter_discovery`
* ``POST /_filters/fields`` — :meth:`ResourceRegistry.field_discovery`
* ``POST /_values`` — :meth:`ResourceRegistry.values`

Every endpoint returns a typed Pydantic model.  Discovery payloads
use a discriminator on ``kind`` so OpenAPI clients narrow without
runtime type checks.

Value endpoints are single-page — autocomplete UX narrows by
typing more characters, not by paginating.
"""

from __future__ import annotations

import enum as _enum_mod
from collections.abc import Sequence  # noqa: TC003 -- runtime use by Pydantic
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    String,
    cast,
    column,
    func,
    literal,
    or_,
    select,
    union_all,
)

from ingot.filter_values import FilterValuesRequest, resolved_limit
from ingot.values_table import values_table

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select


# =============================================================================
# Operator vocabulary.
# =============================================================================


class FilterOperator(_enum_mod.StrEnum):
    """Closed set of operators a filter field may declare."""

    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IN = "in"
    IS_NULL = "is_null"


# =============================================================================
# Field specs — one frozen dataclass per ``FilterValueKind`` from
# :mod:`be.config.schema`.  ``Ref`` covers both cross-resource FK and
# self-reference cases (codegen translates ``values: "self"`` to a
# ``Ref`` targeting the resource's own slug).
# =============================================================================


@dataclass(frozen=True)
class Enum:
    """Enum-typed filter field.

    Discovery emits ``{value, label}`` choices; the values endpoint
    serves the same list ``q``-filterable through a Postgres
    ``VALUES`` clause.
    """

    name: str
    enum_class: type[_enum_mod.Enum]
    operators: tuple[str, ...] = ("eq", "in")
    kind: Literal["enum"] = "enum"


@dataclass(frozen=True)
class FreeText:
    """String-column filter; values endpoint is DISTINCT ILIKE."""

    name: str
    operators: tuple[str, ...] = ("eq", "contains", "starts_with")
    column: str | None = None
    """Override when the searchable column differs from :attr:`name`."""
    kind: Literal["free_text"] = "free_text"


@dataclass(frozen=True)
class Ref:
    """Filter pointing at another resource (or this one).

    The values endpoint dispatches to the *target*'s search; targets
    without a configured :class:`SearchSpec` fall back to a
    pk-ordered page.
    """

    name: str
    target: str
    operators: tuple[str, ...] = ("eq", "in")
    kind: Literal["ref"] = "ref"


@dataclass(frozen=True)
class LiteralField:
    """Numeric / date / datetime input rendered natively on the FE."""

    name: str
    type: str
    operators: tuple[str, ...] = ("eq", "gt", "gte", "lt", "lte")
    kind: Literal["literal"] = "literal"


@dataclass(frozen=True)
class Bool:
    """Boolean toggle."""

    name: str
    operators: tuple[str, ...] = ("eq",)
    kind: Literal["bool"] = "bool"


FilterField = Enum | FreeText | Ref | LiteralField | Bool
"""Sum of every supported filter-field shape."""


# =============================================================================
# Typed discovery payload models.  The ``ValuesDescriptor`` union is
# discriminated on ``kind``; the FE-side OpenAPI client narrows on
# field shape automatically.
# =============================================================================


class Choice(BaseModel):
    """One ``{value, label}`` pair in an enum field's discovery payload."""

    value: str
    label: str


class EnumValuesDescriptor(BaseModel):
    """Discovery descriptor for an :class:`Enum` field."""

    kind: Literal["enum"] = "enum"
    choices: list[Choice]


class FreeTextValuesDescriptor(BaseModel):
    """Discovery descriptor for a :class:`FreeText` field."""

    kind: Literal["free_text"] = "free_text"


class RefValuesDescriptor(BaseModel):
    """Discovery descriptor for a :class:`Ref` field.

    ``target`` is the slug the FE should send as ``body.resource``
    when calling ``POST /_values`` to populate this dropdown.
    """

    kind: Literal["ref"] = "ref"
    target: str


class LiteralValuesDescriptor(BaseModel):
    """Discovery descriptor for a :class:`LiteralField`."""

    kind: Literal["literal"] = "literal"
    type: str


class BoolValuesDescriptor(BaseModel):
    """Discovery descriptor for a :class:`Bool` field."""

    kind: Literal["bool"] = "bool"


ValuesDescriptor = Annotated[
    EnumValuesDescriptor
    | FreeTextValuesDescriptor
    | RefValuesDescriptor
    | LiteralValuesDescriptor
    | BoolValuesDescriptor,
    Field(discriminator="kind"),
]
"""Discriminated union of every per-field discovery descriptor."""


class FieldDiscovery(BaseModel):
    """Discovery payload for one filterable field on a resource."""

    field: str
    operators: list[FilterOperator]
    values: ValuesDescriptor


class ResourceDiscovery(BaseModel):
    """Discovery payload for one resource's filters + search.

    ``supports_search`` advertises whether ``POST /_values`` (with
    no ``fields``) returns search-shaped results.  False means the
    endpoint still works but pages by pk only.
    """

    resource: str
    filters: list[FieldDiscovery]
    supports_search: bool = False


class ProjectDiscovery(BaseModel):
    """Project-wide discovery payload.

    ``resources`` is a list (not a dict) so the codegen layer can
    wrap it in a discriminated union over ``resource``.
    """

    resources: list[ResourceDiscovery]


class FieldsDiscovery(BaseModel):
    """One :class:`FieldDiscovery` per requested ``(resource, field)``."""

    fields: list[FieldDiscovery]


# =============================================================================
# Discovery request bodies.
# =============================================================================


class FilterDiscoveryRequest(BaseModel):
    """Body for ``POST /_filters``.

    ``resources`` selects which resources appear in the response:
    ``None`` (default) → every registered resource; empty list →
    none; one or more slugs → that subset, in order.

    Typed as :class:`~collections.abc.Sequence` so codegen-side
    callers can pass a narrower ``list[ResourceSlug]`` (a
    ``Literal``-typed list) without an invariance error.
    """

    resources: Sequence[str] | None = None


class FieldRef(BaseModel):
    """A pointer to one filter field on one resource."""

    resource: str
    field: str


class FieldDiscoveryRequest(BaseModel):
    """Body for ``POST /_filters/fields``.

    Carries a list of ``(resource, field)`` references; the
    response preserves request order.
    """

    fields: list[FieldRef] = Field(default_factory=list)


# =============================================================================
# Values response.
# =============================================================================


class ValuesPage(BaseModel):
    """Response shape for ``POST /_values``.

    Single-page only — autocomplete UX narrows by typing more
    characters, not by paginating.  ``results`` is
    ``[{"value": ..., "label": ...}]`` for enum / free-text /
    single-field paths and the consumer's link-payload shape
    (already ``model_dump``-ed) for resource search.  Multi-column
    union results add a ``"field"`` key indicating the source
    column.
    """

    results: list[dict[str, Any]]


# =============================================================================
# Resource entry + search spec.
# =============================================================================


@dataclass(frozen=True)
class SearchSpec:
    """Resource-level search configuration.

    Two modes:

    * **ILIKE** (no ``vector_column``) — ``columns`` are OR'd
      via ILIKE on the search query, results paginate by pk.
    * **tsvector** — the named column is matched with
      ``@@ websearch_to_tsquery(query)`` and ranked via
      ``ts_rank``.  Pairs with the pgcraft-generated tsvector
      column on the consumer's model.

    ``link`` shapes each resulting row into the public link
    payload via the consumer's builder.
    """

    columns: tuple[str, ...]
    link: Callable[[Any, Any], Awaitable[Any]]
    vector_column: str | None = None


@dataclass(frozen=True)
class ResourceEntry:
    """One resource's filter declaration, registry-side."""

    model: type
    pk: str
    fields: tuple[FilterField, ...] = ()
    search: SearchSpec | None = None


# =============================================================================
# Registry — public facade.
# =============================================================================


class ResourceRegistry:
    """Project-wide discovery + value-provider dispatcher.

    Construct with a ``{slug: ResourceEntry}`` map at module load
    time; the four generated route handlers call the
    ``filter_discovery`` / ``field_discovery`` / ``values``
    methods on it.  Stateless after construction — safe to share
    across requests.
    """

    def __init__(self, entries: dict[str, ResourceEntry]) -> None:
        """Copy *entries* so the caller can mutate their original."""
        self._entries: dict[str, ResourceEntry] = dict(entries)

    def resources(self) -> list[str]:
        """Return the registered resource slugs, in declaration order."""
        return list(self._entries)

    # -------- Discovery --------

    def filter_discovery(
        self, request: FilterDiscoveryRequest
    ) -> ProjectDiscovery:
        """Per-resource discovery, narrowed by ``request.resources``.

        ``None`` → every registered resource; an explicit list →
        that subset (empty list → no resources), in request order.
        404 on unknown slugs.
        """
        slugs = (
            list(self._entries)
            if request.resources is None
            else list(request.resources)
        )
        return ProjectDiscovery(
            resources=[self._discover_resource(slug) for slug in slugs],
        )

    def field_discovery(
        self, request: FieldDiscoveryRequest
    ) -> FieldsDiscovery:
        """One :class:`FieldDiscovery` per requested ``(resource, field)``.

        Order is preserved; 404 on any unknown resource or field.
        """
        return FieldsDiscovery(
            fields=[self._discover_field(ref) for ref in request.fields],
        )

    def _discover_resource(self, slug: str) -> ResourceDiscovery:
        self._require_entry(slug)
        entry = self._entries[slug]
        return ResourceDiscovery(
            resource=slug,
            filters=[_discover_filter(spec) for spec in entry.fields],
            supports_search=entry.search is not None,
        )

    def _discover_field(self, ref: FieldRef) -> FieldDiscovery:
        entry = self._require_entry(ref.resource)
        spec = _find_field(entry, ref.field)

        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown filter field: {ref.resource}.{ref.field}",
            )

        return _discover_filter(spec)

    # -------- Values --------

    async def values(
        self,
        *,
        resource: str,
        fields: Sequence[str],
        request: FilterValuesRequest,
        db: AsyncSession,
        session: Any = None,
    ) -> ValuesPage:
        """Run a value-provider request.

        ``fields=[]`` runs a resource-level search (link-shaped
        results); any non-empty ``fields`` runs a pg_trgm union
        ranked together, which degrades naturally to a single
        ``%``-match when only one field is named.  The FE already
        has every enum member from discovery, so single-field Enum
        calls without ``q`` returning nothing isn't a regression.
        """
        entry = self._require_entry(resource)

        if not fields:
            return await _run_resource_search(entry, request, db, session)

        return await self._run_multi_column_search(entry, fields, request, db)

    # -------- Internal helpers --------

    def _require_entry(self, resource: str) -> ResourceEntry:
        entry = self._entries.get(resource)

        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown resource: {resource}",
            )

        return entry

    # -------- Multi-column trigram union --------

    async def _run_multi_column_search(
        self,
        entry: ResourceEntry,
        fields: Sequence[str],
        request: FilterValuesRequest,
        db: AsyncSession,
    ) -> ValuesPage:
        """UNION ``(field, value, label, score)`` per field, ranked together.

        Works for any non-empty ``fields``; one field is just a
        one-arm union.  Each field dispatches by its
        :class:`FilterField` kind so heterogeneous filters compose
        into one search box:

        * **FreeText** — trigram on the source column.
        * **Enum** — trigram on the enum's member names via a
          ``VALUES`` table (so unused enum members still surface).
        * **Ref** — trigram on the target resource's first
          configured search column; ``value`` is the target's
          stringified pk so the FE can plug it straight into a
          filter operand.
        * **Bool** / **LiteralField** — 404; they have no text to
          score against.

        Without ``q`` the union returns nothing (relevance scoring
        is meaningless without a query).  Requires ``pg_trgm``.
        """
        # Validate field names up-front so unknown / unscorable
        # fields surface as 404 even when ``q`` is empty.
        for name in fields:
            spec = _find_field(entry, name)

            if spec is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown filter field: {name}",
                )

            if isinstance(spec, (Bool, LiteralField)):
                raise HTTPException(
                    status_code=404,
                    detail=f"Field {name!r} has no value provider",
                )

        if not request.q:
            return ValuesPage(results=[])

        sub_queries = [
            self._trigram_subquery(entry, name, request.q) for name in fields
        ]
        statement = (
            union_all(*sub_queries)
            .order_by(column("score").desc(), column("value").asc())
            .limit(resolved_limit(request.limit))
        )
        rows = (await db.execute(statement)).all()
        return ValuesPage(
            results=[
                {
                    "field": row.field,
                    "value": row.value,
                    "label": row.label,
                    "score": float(row.score),
                }
                for row in rows
            ],
        )

    def _trigram_subquery(
        self,
        entry: ResourceEntry,
        field_name: str,
        query: str,
    ) -> Select[Any]:
        """Build the trigram subquery for one field in the union.

        Dispatches on the field's :class:`FilterField` kind so each
        type contributes the right ``(value, label, score)`` shape:
        a FreeText column comes straight from the source row, an
        Enum from the enum class' members table, a Ref from the
        target resource's link column.
        """
        spec = _find_field(entry, field_name)

        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown filter field: {field_name}",
            )

        if isinstance(spec, FreeText):
            column_expr = getattr(entry.model, spec.column or spec.name)
            return (
                select(
                    literal(spec.name).label("field"),
                    column_expr.label("value"),
                    column_expr.label("label"),
                    func.similarity(column_expr, query).label("score"),
                )
                .distinct()
                .where(column_expr.op("%")(query))
            )

        if isinstance(spec, Enum):
            members_table = values_table(
                _ChoiceRow,
                [
                    _ChoiceRow(value=str(member.value), label=member.name)
                    for member in spec.enum_class
                ],
                name=f"enum_{spec.name}",
            )
            label_col = members_table.c.label
            return select(
                literal(spec.name).label("field"),
                members_table.c.value.label("value"),
                label_col.label("label"),
                func.similarity(label_col, query).label("score"),
            ).where(label_col.op("%")(query))

        if isinstance(spec, Ref):
            target = self._require_entry(spec.target)
            target_pk = getattr(target.model, target.pk)

            # Label by the target's first configured search column
            # when present; otherwise fall back to its stringified
            # pk.  Must be text-shaped for ``similarity()`` to
            # compose.
            if target.search and target.search.columns:
                target_label = getattr(target.model, target.search.columns[0])

            else:
                target_label = cast(target_pk, String)

            return select(
                literal(spec.name).label("field"),
                cast(target_pk, String).label("value"),
                target_label.label("label"),
                func.similarity(target_label, query).label("score"),
            ).where(target_label.op("%")(query))

        # Bool / LiteralField — no text to score against; the FE
        # renders these natively without calling ``/_values``.
        raise HTTPException(
            status_code=404,
            detail=f"Field {field_name!r} has no value provider",
        )


# =============================================================================
# Discovery payload helpers.
# =============================================================================


def _discover_filter(spec: FilterField) -> FieldDiscovery:
    values: ValuesDescriptor

    if isinstance(spec, Enum):
        values = EnumValuesDescriptor(
            choices=[
                Choice(value=str(member.value), label=member.name)
                for member in spec.enum_class
            ],
        )

    elif isinstance(spec, FreeText):
        values = FreeTextValuesDescriptor()

    elif isinstance(spec, Ref):
        values = RefValuesDescriptor(target=spec.target)

    elif isinstance(spec, LiteralField):
        values = LiteralValuesDescriptor(type=spec.type)

    else:  # Bool
        values = BoolValuesDescriptor()

    return FieldDiscovery(
        field=spec.name,
        operators=[FilterOperator(op) for op in spec.operators],
        values=values,
    )


def _find_field(entry: ResourceEntry, name: str) -> FilterField | None:
    return next(
        (field for field in entry.fields if field.name == name),
        None,
    )


# =============================================================================
# Value-provider runners.
#
# Every runner is single-page — autocomplete UX narrows by typing
# more characters rather than paginating, so the registry doesn't
# carry cursor / over-fetch / next_cursor bookkeeping.  Each
# runner: build a SELECT, optional WHERE on ``q``, ORDER BY,
# LIMIT, execute, shape rows.  That's it.
# =============================================================================


async def _run_resource_search(
    entry: ResourceEntry,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
) -> ValuesPage:
    """Resource-level search; one page of link-shaped results.

    Branches by ``q`` and search config:

    * ``q`` + tsvector → ``vector @@ websearch_to_tsquery``,
      ``ORDER BY ts_rank DESC``.
    * ``q`` + ILIKE columns → OR'd ILIKE WHERE, ``ORDER BY pk``.
    * Otherwise → unfiltered ``ORDER BY pk``.

    Entries without a :class:`SearchSpec` get a pk-only page with
    ``{"value": pk, "label": str(pk)}`` rows so ref-only resources
    still drive a usable autocomplete.
    """
    search = entry.search
    query = request.q
    limit = resolved_limit(request.limit)
    pk_column = getattr(entry.model, entry.pk)

    if search and query and search.vector_column is not None:
        vector = getattr(entry.model, search.vector_column)
        tsquery = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank(vector, tsquery)
        statement: Select[Any] = (
            select(entry.model)
            .where(vector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit)
        )

    else:
        statement = select(entry.model).order_by(pk_column.asc()).limit(limit)

        if search and query and search.columns:
            ilike_columns = [
                getattr(entry.model, name) for name in search.columns
            ]
            statement = statement.where(
                or_(*[col.ilike(f"%{query}%") for col in ilike_columns])
            )

    rows = list((await db.execute(statement)).scalars().all())

    if search is None:
        return ValuesPage(
            results=[
                {
                    "value": str(getattr(row, entry.pk)),
                    "label": str(getattr(row, entry.pk)),
                }
                for row in rows
            ],
        )

    results: list[dict[str, Any]] = []

    for row in rows:
        link = await search.link(row, session)
        results.append(
            link.model_dump() if hasattr(link, "model_dump") else link
        )

    return ValuesPage(results=results)


@dataclass(frozen=True)
class _ChoiceRow:
    """One ``(value, label)`` row for VALUES-clause enum tables."""

    value: str
    label: str
