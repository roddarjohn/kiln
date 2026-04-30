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

Pagination is single-key keyset / offset depending on the path —
documented inline on each runner.  The cursor wire format is just
the previous-key value as a string; each runner knows how to
decode for its own ordering.
"""

from __future__ import annotations

import enum as _enum_mod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, literal, or_, select, union_all

from ingot.filter_values import FilterValuesRequest, resolved_limit
from ingot.pagination import apply_keyset_pagination
from ingot.values_table import values_table

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

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
    """Body for ``POST /_filters/fields`` — list of ``(resource, field)``
    references.  Response preserves order.
    """

    fields: list[FieldRef] = Field(default_factory=list)


# =============================================================================
# Values response.
# =============================================================================


class ValuesPage(BaseModel):
    """Response shape for ``POST /_values``.

    ``results`` is ``[{"value": ..., "label": ...}]`` for
    enum / free-text / single-field paths and the consumer's
    link-payload shape (already ``model_dump``-ed) for resource
    search.  Multi-column union results add a ``"field"`` key
    indicating the source column.
    """

    results: list[dict[str, Any]]
    next_cursor: str | None = None


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
        # Copy so callers can keep mutating their original.
        self._entries: dict[str, ResourceEntry] = dict(entries)

    def resources(self) -> list[str]:
        """Registered resource slugs, in declaration order."""
        return list(self._entries)

    # -------- Discovery --------

    def filter_discovery(
        self, request: FilterDiscoveryRequest
    ) -> ProjectDiscovery:
        """Per-resource discovery, narrowed by ``request.resources``.

        ``None`` → every registered resource; otherwise the named
        subset, in request order.  404 on unknown slugs.
        """
        slugs = (
            list(self._entries)
            if request.resources is None
            else [self._require_slug(slug) for slug in request.resources]
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
        """Dispatch a value-provider request to the right runner.

        Three cases by ``len(fields)``:

        * ``0`` — resource-level search.
        * ``1`` — per-field dispatch (Enum / FreeText / Ref;
          Bool / Literal raise 404 — they have no value provider).
        * ``2+`` — multi-column union: distinct values from each
          named column, tagged with the source field for FE-side
          grouping.
        """
        entry = self._require_entry(resource)

        if not fields:
            return await _run_resource_search(entry, request, db, session)

        if len(fields) == 1:
            return await self._dispatch_single_field(
                entry, fields[0], request, db, session
            )

        return await _run_multi_column_search(entry, fields, request, db)

    async def _dispatch_single_field(
        self,
        entry: ResourceEntry,
        field_name: str,
        request: FilterValuesRequest,
        db: AsyncSession,
        session: Any,
    ) -> ValuesPage:
        spec = _find_field(entry, field_name)

        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown filter field: {field_name}",
            )

        if isinstance(spec, Enum):
            return await _run_enum_values(spec.enum_class, request, db)

        if isinstance(spec, FreeText):
            return await _run_free_text_values(entry, spec, request, db)

        if isinstance(spec, Ref):
            target_entry = self._require_entry(spec.target)
            return await _run_resource_search(
                target_entry, request, db, session
            )

        # Bool / LiteralField — no value provider, FE renders natively.
        raise HTTPException(
            status_code=404,
            detail=f"Field {field_name!r} has no value provider",
        )

    # -------- Internal helpers --------

    def _require_entry(self, resource: str) -> ResourceEntry:
        entry = self._entries.get(resource)

        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown resource: {resource}",
            )

        return entry

    def _require_slug(self, slug: str) -> str:
        self._require_entry(slug)  # 404 on unknown
        return slug


# =============================================================================
# Discovery payload helpers.
# =============================================================================


def _discover_filter(spec: FilterField) -> FieldDiscovery:
    return FieldDiscovery(
        field=spec.name,
        operators=[FilterOperator(op) for op in spec.operators],
        values=_discover_values(spec),
    )


def _discover_values(spec: FilterField) -> ValuesDescriptor:
    if isinstance(spec, Enum):
        return EnumValuesDescriptor(
            choices=[
                Choice(value=str(member.value), label=member.name)
                for member in spec.enum_class
            ],
        )

    if isinstance(spec, FreeText):
        return FreeTextValuesDescriptor()

    if isinstance(spec, Ref):
        return RefValuesDescriptor(target=spec.target)

    if isinstance(spec, LiteralField):
        return LiteralValuesDescriptor(type=spec.type)

    return BoolValuesDescriptor()  # Bool


def _find_field(entry: ResourceEntry, name: str) -> FilterField | None:
    return next(
        (field for field in entry.fields if field.name == name),
        None,
    )


# =============================================================================
# Resource-level search runner — pk-keyset pagination, link-shaped
# results.  Three branches: tsvector, ILIKE, unfiltered.  When the
# entry has no SearchSpec, a synthetic one (no columns, default
# pk → label link) is built so the runner has a uniform shape.
# =============================================================================


async def _run_resource_search(
    entry: ResourceEntry,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
) -> ValuesPage:
    """Resource-level search; ORDER BY pk in every branch."""
    search = entry.search or _default_search_spec(entry)
    query = request.q

    if query and search.vector_column is not None:
        return await _run_tsvector_search(
            entry, search, query, request, db, session
        )

    statement = select(entry.model)

    if query and search.columns:
        ilike_columns = [getattr(entry.model, name) for name in search.columns]
        statement = statement.where(
            or_(*[col.ilike(f"%{query}%") for col in ilike_columns])
        )

    return await _execute_pk_search(
        statement, entry, search, request, db, session
    )


async def _execute_pk_search(
    statement: Select[Any],
    entry: ResourceEntry,
    search: SearchSpec,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
) -> ValuesPage:
    """Apply pk-keyset pagination, execute, shape via link builder."""
    pk_column = getattr(entry.model, entry.pk)
    page_size = resolved_limit(request.limit)
    statement, _ = apply_keyset_pagination(
        statement.order_by(pk_column.asc()),
        entry.model,
        cursor=request.cursor or None,
        cursor_field=entry.pk,
        page_size=page_size,
        max_page_size=page_size,
    )
    rows = list((await db.execute(statement)).scalars().all())
    page, next_cursor = _trim_page(
        rows,
        page_size,
        cursor_from=lambda last: str(getattr(last, entry.pk)),
    )
    items = [await _shape_link(row, search, session) for row in page]
    return ValuesPage(results=items, next_cursor=next_cursor)


async def _run_tsvector_search(
    entry: ResourceEntry,
    search: SearchSpec,
    query: str,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
) -> ValuesPage:
    """``q`` + tsvector — ORDER BY ts_rank DESC, cursor on rank.

    Single-column ordering means tied ranks could skip a row at
    page boundaries, but ts_rank values diverge enough in
    practice that the autocomplete UX tolerates it.
    """
    assert search.vector_column is not None  # noqa: S101 — guarded by caller
    vector = getattr(entry.model, search.vector_column)
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank(vector, tsquery).label("_rank")
    page_size = resolved_limit(request.limit)
    statement: Select[Any] = (
        select(entry.model, rank)
        .where(vector.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(page_size + 1)
    )
    previous_rank = _parse_float_cursor(request.cursor)

    if previous_rank is not None:
        statement = statement.where(rank < previous_rank)

    raw_rows = (await db.execute(statement)).all()
    rows = [(row[0], float(row[1])) for row in raw_rows]
    page, next_cursor = _trim_page(
        rows,
        page_size,
        cursor_from=lambda last: repr(last[1]),
    )
    instances = [instance for instance, _ in page]
    items = [await _shape_link(row, search, session) for row in instances]
    return ValuesPage(results=items, next_cursor=next_cursor)


async def _shape_link(
    instance: Any, search: SearchSpec, session: Any
) -> dict[str, Any]:
    link = await search.link(instance, session)
    return link.model_dump() if hasattr(link, "model_dump") else link


def _default_search_spec(entry: ResourceEntry) -> SearchSpec:
    """Synthetic SearchSpec for entries without one configured.

    Empty ``columns`` (no ``q``-filtering) and a link builder that
    emits ``{"value": pk, "label": str(pk)}`` so ref-only
    resources still get a usable autocomplete.
    """
    pk_attr = entry.pk

    async def _pk_link(instance: Any, _session: Any) -> dict[str, Any]:
        pk_value = getattr(instance, pk_attr)
        return {"value": str(pk_value), "label": str(pk_value)}

    return SearchSpec(columns=(), link=_pk_link)


# =============================================================================
# Per-field value providers — enum + free-text.  Both use the same
# offset-paged scalar pipeline; offset is fine here since the result
# sets are small and stable enough that keyset bookkeeping isn't
# worth the complexity.
# =============================================================================


async def _run_free_text_values(
    entry: ResourceEntry,
    spec: FreeText,
    request: FilterValuesRequest,
    db: AsyncSession,
) -> ValuesPage:
    """DISTINCT column values, optionally ILIKE-filtered."""
    column = getattr(entry.model, spec.column or spec.name)
    statement = select(column).distinct().order_by(column.asc())

    if request.q:
        statement = statement.where(column.ilike(f"%{request.q}%"))

    page, next_cursor = await _execute_offset_scalars(statement, request, db)
    return ValuesPage(
        results=[{"value": value, "label": value} for value in page],
        next_cursor=next_cursor,
    )


@dataclass(frozen=True)
class _LabelRow:
    """One-column row for the enum-values ``VALUES`` clause."""

    label: str


async def _run_enum_values(
    enum_class: type[_enum_mod.Enum],
    request: FilterValuesRequest,
    db: AsyncSession,
) -> ValuesPage:
    """Enum members served via Postgres ``VALUES`` — same pipeline
    as SQL tables.  Values map back to the enum Python-side; only
    labels make the round-trip.
    """
    label_to_value = {member.name: str(member.value) for member in enum_class}
    table = values_table(
        _LabelRow,
        [_LabelRow(label=name) for name in label_to_value],
        name="enum_values",
    )
    label = table.c.label
    statement = select(label).order_by(label.asc())

    if request.q:
        statement = statement.where(label.ilike(f"%{request.q}%"))

    page, next_cursor = await _execute_offset_scalars(statement, request, db)
    return ValuesPage(
        results=[
            {"value": label_to_value[name], "label": name} for name in page
        ],
        next_cursor=next_cursor,
    )


# =============================================================================
# Multi-column union — distinct values from each named column,
# UNION'd together and tagged with the source field name.  Result
# items carry a ``"field"`` key so the FE knows which column each
# value came from and can group / sort however it likes.
# =============================================================================


async def _run_multi_column_search(
    entry: ResourceEntry,
    fields: Sequence[str],
    request: FilterValuesRequest,
    db: AsyncSession,
) -> ValuesPage:
    """UNION distinct ``(field, value)`` pairs from each named column.

    Single page only — capped at ``request.limit`` (default 50).
    Cross-field relevance matching for autocomplete UX, where the
    user narrows by typing rather than paginating.
    """

    def per_field(name: str) -> Select[Any]:
        column = getattr(entry.model, name)
        statement = select(
            literal(name).label("field"),
            column.label("value"),
        ).distinct()
        return (
            statement.where(column.ilike(f"%{request.q}%"))
            if request.q
            else statement
        )

    statement = (
        union_all(*[per_field(name) for name in fields])
        .order_by("value")
        .limit(resolved_limit(request.limit))
    )
    rows = (await db.execute(statement)).all()
    return ValuesPage(
        results=[
            {"field": row.field, "value": row.value, "label": row.value}
            for row in rows
        ],
        next_cursor=None,
    )


# =============================================================================
# Offset-paged scalar pipeline — used by enum + free-text.
# =============================================================================


async def _execute_offset_scalars(
    statement: Select[Any],
    request: FilterValuesRequest,
    db: AsyncSession,
) -> tuple[list[Any], str | None]:
    """Paginate *statement* by offset, return ``(scalar rows, next cursor)``."""
    page_size = resolved_limit(request.limit)
    offset = _parse_int_cursor(request.cursor)
    rows = list(
        (await db.execute(statement.offset(offset).limit(page_size + 1)))
        .scalars()
        .all()
    )
    has_more, page = _has_more(rows, page_size)
    next_cursor = str(offset + page_size) if has_more else None
    return page, next_cursor


async def _fetch_offset(
    statement: Select[Any],
    request: FilterValuesRequest,
    db: AsyncSession,
) -> list[Any]:
    """Execute *statement* with ``OFFSET / LIMIT n+1`` for offset paging."""
    page_size = resolved_limit(request.limit)
    offset = _parse_int_cursor(request.cursor)
    return list(
        (await db.execute(statement.offset(offset).limit(page_size + 1))).all()
    )


# =============================================================================
# Cursor + page-trim helpers.
#
# Three cursor formats over the wire (all bare strings):
#
# * Resource search keyset: stringified pk value (e.g. ``"42"``,
#   UUID, ...) — passed through to a SQLAlchemy ``WHERE pk > prev``.
# * Tsvector keyset: stringified float (``repr(0.1234)``) — decoded
#   via :func:`_parse_float_cursor`.
# * Offset (enum / free-text / multi-column): stringified int —
#   decoded via :func:`_parse_int_cursor`.
#
# Each runner knows which mode it's in so the wire format doesn't
# need a tag.
# =============================================================================


def _trim_page[T](
    rows: list[T],
    page_size: int,
    *,
    cursor_from: Callable[[T], str],
) -> tuple[list[T], str | None]:
    """Trim the over-fetch row, build a keyset cursor from the last row."""
    has_more, page = _has_more(rows, page_size)
    next_cursor = cursor_from(page[-1]) if has_more and page else None
    return page, next_cursor


def _has_more[T](rows: list[T], page_size: int) -> tuple[bool, list[T]]:
    """Detect over-fetch and return ``(has_more, trimmed_page)``."""
    return len(rows) > page_size, rows[:page_size]


def _next_offset_cursor(previous: str | None, page_size: int) -> str:
    return str(_parse_int_cursor(previous) + page_size)


def _parse_int_cursor(cursor: str | None) -> int:
    """Decode a stringified-int cursor; ``0`` for missing / invalid."""
    if not cursor:
        return 0

    try:
        return max(0, int(cursor))

    except ValueError:
        return 0


def _parse_float_cursor(cursor: str | None) -> float | None:
    """Decode a stringified-float cursor; ``None`` for missing / invalid."""
    if not cursor:
        return None

    try:
        return float(cursor)

    except ValueError:
        return None
