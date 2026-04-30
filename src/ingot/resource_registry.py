"""Project-wide resource registry: discovery + value-provider engine.

Generated codegen emits one :class:`ResourceRegistry` per project,
populated declaratively with one :class:`ResourceEntry` per
resource.  Today the registry covers filter discovery and
value-provider dispatch; future work folds in actions, dump
schemas, and other resource-scoped concerns under the same map.

Project-wide route handlers (``POST /_filters``,
``POST /_filters/fields``, ``POST /_values/{resource}``,
``POST /_values/{resource}/{field}``) delegate everything to
:meth:`ResourceRegistry.filter_discovery`,
:meth:`ResourceRegistry.field_discovery`, and
:meth:`ResourceRegistry.values` — they hold no logic of their own.

All endpoints return typed Pydantic models.  Discovery is a
discriminated union (``kind``) so the FE-side OpenAPI client narrows
on field shape automatically.  Values are returned as
:class:`ValuesPage` carrying a list of dicts plus an optional
``next_cursor``.

Pagination is single-column keyset everywhere — one ordering key,
one cursor:

* No query: ORDER BY pk; cursor is the previous pk.
* Free-text + query: ORDER BY column; cursor is the previous
  column value.
* Resource search + query (ILIKE): ORDER BY pk; cursor is the
  previous pk.
* Resource search + query (tsvector): ORDER BY ts_rank DESC;
  cursor is the previous rank.

Enum search runs through the same SQL-based pipeline via
:func:`ingot.values_table.values_table` — the enum becomes a
``VALUES (...)`` selectable, then the same pagination/ILIKE
machinery applies.
"""

from __future__ import annotations

import enum as _enum_mod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from ingot.filter_values import FilterValuesRequest, resolved_limit
from ingot.pagination import apply_keyset_pagination
from ingot.values_table import values_table

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select


# -------------------------------------------------------------------
# Operator vocabulary.
#
# Lifted to a real Enum so the OpenAPI surface emits a string
# enum (rather than ``string``) and the FE OpenAPI client knows
# the closed set.
# -------------------------------------------------------------------


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


# -------------------------------------------------------------------
# Field specs.  One frozen dataclass per ``FilterValueKind`` from
# :mod:`be.config.schema`; the union :data:`FilterField` keeps callers
# from having to import the leaves individually.
# -------------------------------------------------------------------


@dataclass(frozen=True)
class Enum:
    """Enum-typed filter field.

    Discovery emits ``{value, label}`` pairs from ``enum_class``
    (computed at request time).  Values endpoint serves the same
    list, ``q``-filterable through a Postgres ``VALUES`` clause so
    the same pagination/keyset machinery applies as for SQL tables.
    """

    name: str
    enum_class: type[_enum_mod.Enum]
    operators: tuple[str, ...] = ("eq", "in")
    kind: Literal["enum"] = "enum"


@dataclass(frozen=True)
class FreeText:
    """String-column filter served via DISTINCT ILIKE on the column.

    ``column`` defaults to :attr:`name`; override when the search
    target differs from the field name (rare).
    """

    name: str
    operators: tuple[str, ...] = ("eq", "contains", "starts_with")
    column: str | None = None
    kind: Literal["free_text"] = "free_text"


@dataclass(frozen=True)
class Ref:
    """Filter pointing at another resource (or this one).

    Covers both the cross-resource FK case and the "filter by my
    own pk" case — both render the same FE affordance
    (autocomplete-by-slug) so they share a single field type.
    Set ``target`` to the slug of the resource whose values
    populate the dropdown; for self-references that's this
    resource's own slug.

    When the target has a configured :class:`SearchSpec`, the
    values endpoint runs that search; when it doesn't, the
    endpoint falls back to "first N rows by pk" so the FE still
    gets something to show.
    """

    name: str
    target: str
    operators: tuple[str, ...] = ("eq", "in")
    kind: Literal["ref"] = "ref"


@dataclass(frozen=True)
class LiteralField:
    """Numeric/date/datetime input rendered natively on the FE.

    No values endpoint — the FE produces values directly from
    user input.
    """

    name: str
    type: str
    operators: tuple[str, ...] = ("eq", "gt", "gte", "lt", "lte")
    kind: Literal["literal"] = "literal"


@dataclass(frozen=True)
class Bool:
    """Boolean toggle.  No values endpoint."""

    name: str
    operators: tuple[str, ...] = ("eq",)
    kind: Literal["bool"] = "bool"


FilterField = Enum | FreeText | Ref | LiteralField | Bool
"""Sum of every supported filter-field shape."""


# -------------------------------------------------------------------
# Typed discovery payload models.
# -------------------------------------------------------------------


class Choice(BaseModel):
    """One ``{value, label}`` pair in an enum field's discovery payload.

    ``value`` is stringified at construction (``str(enum.value)``)
    so the SQL ``VALUES`` path can build a homogeneous column
    type regardless of the underlying enum (``StrEnum`` /
    ``IntEnum`` / mixed-type members all flatten to ``str``).
    """

    value: str
    label: str


class EnumValuesDescriptor(BaseModel):
    """Discovery descriptor for an :class:`Enum` field."""

    kind: Literal["enum"] = "enum"
    choices: list[Choice]
    endpoint: str


class FreeTextValuesDescriptor(BaseModel):
    """Discovery descriptor for a :class:`FreeText` field."""

    kind: Literal["free_text"] = "free_text"
    endpoint: str


class RefValuesDescriptor(BaseModel):
    """Discovery descriptor for a :class:`Ref` field.

    ``endpoint`` always points at the target resource's
    ``/_values/{target}`` route — the registry serves a search
    when one's configured and falls back to a pk-ordered page
    otherwise, so the FE has a single endpoint to call regardless.
    """

    kind: Literal["ref"] = "ref"
    target: str
    endpoint: str


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

    ``supports_search`` advertises whether ``POST /_values/<slug>``
    is meaningful.  When ``False``, the endpoint still exists (it
    falls back to a pk-ordered page) but its results aren't
    search-shaped.  The endpoint URL is implicit
    (``/_values/<slug>``) so we don't dump it.
    """

    resource: str
    filters: list[FieldDiscovery]
    supports_search: bool = False


class ProjectDiscovery(BaseModel):
    """Discovery payload covering every registered resource.

    ``resources`` is a list (rather than a dict) so the codegen
    layer can wrap it in a discriminated union over the
    ``resource`` field — that's what gives the FE-side OpenAPI
    client real per-resource narrowing.
    """

    resources: list[ResourceDiscovery]


class FieldsDiscovery(BaseModel):
    """Response shape for ``POST /_filters/fields``.

    A list of resolved :class:`FieldDiscovery` payloads, one per
    requested ``(resource, field)`` pair, in the request order.
    """

    fields: list[FieldDiscovery]


# -------------------------------------------------------------------
# Discovery request bodies.
# -------------------------------------------------------------------


class FilterDiscoveryRequest(BaseModel):
    """Request body for ``POST /_filters``.

    ``resources`` selects which resources appear in the response:

    * ``None`` (the default) — every registered resource.
    * empty list — none.
    * one or more slugs — that subset, in request order.
    """

    resources: list[str] | None = None


class FieldRef(BaseModel):
    """A pointer to one filter field on one resource."""

    resource: str
    field: str


class FieldDiscoveryRequest(BaseModel):
    """Request body for ``POST /_filters/fields``.

    ``fields`` is a list of ``(resource, field)`` references; the
    response carries one :class:`FieldDiscovery` per ref, in the
    same order.
    """

    fields: list[FieldRef] = Field(default_factory=list)


# -------------------------------------------------------------------
# Typed values-page model.
# -------------------------------------------------------------------


class ValuesPage(BaseModel):
    """Response shape for value-provider endpoints.

    ``results`` is ``[{"value": ..., "label": ...}]`` for
    enum/free-text fields and the consumer's link-payload shape
    (already ``model_dump``-ed) for resource search and ref
    dispatch.
    """

    results: list[dict[str, Any]]
    next_cursor: str | None = None


# -------------------------------------------------------------------
# Resource-level search + entry.
# -------------------------------------------------------------------


@dataclass(frozen=True)
class SearchSpec:
    """Resource-level ``POST /_values/{resource}`` search configuration.

    Two search modes:

    - **ILIKE fallback** (no ``vector_column``): ``columns`` are
      OR'd via ILIKE on the search query, results paginate by pk.
    - **tsvector mode** (``vector_column`` set): the column is
      matched via ``@@ websearch_to_tsquery(query)`` and ranked
      via ``ts_rank(...)``.  Pairs with the pgcraft-generated
      tsvector column on the consumer's model.

    ``link`` shapes each result into the public link payload via
    the consumer's builder.
    """

    columns: tuple[str, ...]
    link: Callable[[Any, Any], Awaitable[Any]]
    """``async (instance, session) -> link``.  In practice the
    codegen drops in the resource's ``LINKS["..."]`` callable."""

    vector_column: str | None = None
    """Name of a Postgres ``tsvector`` column on the model.  When
    set, the search runs ``vector_column @@ websearch_to_tsquery(q)``
    and orders by ``ts_rank``; ILIKE on :attr:`columns` is skipped.
    ``None`` falls back to ILIKE."""


@dataclass(frozen=True)
class ResourceEntry:
    """A single resource's filter declaration, registry-side."""

    model: type
    pk: str
    fields: tuple[FilterField, ...] = ()
    search: SearchSpec | None = None


# -------------------------------------------------------------------
# Registry.
# -------------------------------------------------------------------


class ResourceRegistry:
    """Project-wide discovery + value-provider dispatcher.

    Construct with ``ResourceRegistry({"item": ResourceEntry(...), ...})``
    at module load time; pass the resulting instance to the four
    generated route handlers.  Stateless after construction —
    safe to share across requests.
    """

    def __init__(self, entries: dict[str, ResourceEntry]) -> None:
        """Store *entries* keyed by resource slug.

        The dict is copied so callers can keep mutating their
        construction-site map without surprising the registry.
        """
        self._entries: dict[str, ResourceEntry] = dict(entries)

    def resources(self) -> list[str]:
        """Return the list of registered resource slugs."""
        return list(self._entries)

    # ---------- Discovery ----------

    def filter_discovery(
        self,
        request: FilterDiscoveryRequest,
    ) -> ProjectDiscovery:
        """Return discovery for the resources named in *request*.

        Raises :class:`fastapi.HTTPException` (404) when any
        requested slug is not registered.
        """
        if request.resources is None:
            slugs: list[str] = list(self._entries)

        else:
            for slug in request.resources:
                self._require_entry(slug)

            slugs = list(request.resources)

        return ProjectDiscovery(
            resources=[self._resource_payload(slug) for slug in slugs],
        )

    def field_discovery(
        self,
        request: FieldDiscoveryRequest,
    ) -> FieldsDiscovery:
        """Return per-field discovery for each ``(resource, field)``.

        Order is preserved.  Unknown resource/field combinations
        raise :class:`fastapi.HTTPException` (404).
        """
        resolved: list[FieldDiscovery] = []

        for ref in request.fields:
            entry = self._require_entry(ref.resource)
            spec = next(
                (
                    candidate
                    for candidate in entry.fields
                    if candidate.name == ref.field
                ),
                None,
            )

            if spec is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Unknown filter field: {ref.resource}.{ref.field}"
                    ),
                )

            resolved.append(self._field_payload(spec))

        return FieldsDiscovery(fields=resolved)

    def _resource_payload(self, resource: str) -> ResourceDiscovery:
        """Build the per-resource discovery payload for *resource*."""
        entry = self._require_entry(resource)
        return ResourceDiscovery(
            resource=resource,
            filters=[self._field_payload(spec) for spec in entry.fields],
            supports_search=entry.search is not None,
        )

    def _field_payload(self, spec: FilterField) -> FieldDiscovery:
        """Build the discovery payload for one field."""
        return FieldDiscovery(
            field=spec.name,
            operators=[FilterOperator(op) for op in spec.operators],
            values=self._values_descriptor(spec),
        )

    def _values_descriptor(self, spec: FilterField) -> ValuesDescriptor:
        """Return the ``values`` block for one field."""
        if isinstance(spec, Enum):
            return EnumValuesDescriptor(
                choices=[
                    Choice(value=str(member.value), label=member.name)
                    for member in spec.enum_class
                ],
                endpoint=f"/_values/{spec.name}",
            )

        if isinstance(spec, FreeText):
            return FreeTextValuesDescriptor(
                endpoint=f"/_values/{spec.name}",
            )

        if isinstance(spec, Ref):
            return RefValuesDescriptor(
                target=spec.target,
                endpoint=f"/_values/{spec.target}",
            )

        if isinstance(spec, LiteralField):
            return LiteralValuesDescriptor(type=spec.type)

        # Bool: nothing extra to attach.
        return BoolValuesDescriptor()

    # ---------- Values ----------

    async def values(
        self,
        *,
        resource: str,
        field: str | None,
        request: FilterValuesRequest,
        db: AsyncSession,
        session: Any = None,
    ) -> ValuesPage:
        """Dispatch a value-provider request to the right code path.

        ``field=None`` runs the resource-level search; a named
        field dispatches by :class:`FilterField` variant —
        ``Enum`` and ``FreeText`` serve their own values, ``Ref``
        recurses into the target resource's search, ``Bool`` and
        ``LiteralField`` 404.
        """
        entry = self._require_entry(resource)

        if field is None:
            return await self._search_resource(entry, request, db, session)

        spec = next(
            (
                candidate
                for candidate in entry.fields
                if candidate.name == field
            ),
            None,
        )

        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown filter field: {field}",
            )

        if isinstance(spec, Enum):
            return await _enum_values(spec.enum_class, request, db)

        if isinstance(spec, FreeText):
            return await _free_text_values(entry, spec, request, db)

        if isinstance(spec, Ref):
            target_entry = self._require_entry(spec.target)
            return await self._search_resource(
                target_entry, request, db, session
            )

        # Bool / LiteralField: no values endpoint.
        raise HTTPException(
            status_code=404,
            detail=f"Field {field!r} has no value provider",
        )

    def _require_entry(self, resource: str) -> ResourceEntry:
        """Look up *resource*; 404 when unknown."""
        entry = self._entries.get(resource)

        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown resource: {resource}",
            )

        return entry

    async def _search_resource(
        self,
        entry: ResourceEntry,
        request: FilterValuesRequest,
        db: AsyncSession,
        session: Any,
    ) -> ValuesPage:
        """Resource-level search; falls back to a pk-ordered page.

        When the entry has a :class:`SearchSpec`, runs the
        configured search (tsvector if a vector column is set,
        ILIKE otherwise) and shapes results through the link
        builder.

        When there's no SearchSpec, returns the first N rows
        ordered by pk with ``{value: pk, label: str(pk)}``
        items — gives the FE *something* to show even for
        ref-only resources without a configured search.
        """
        if entry.search is None:
            return await _pk_only_search(entry, request, db)

        search = entry.search
        primary_key_column = getattr(entry.model, entry.pk)
        query = request.q

        if query and search.vector_column is not None:
            return await _run_tsvector_search(
                entry=entry,
                search=search,
                query=query,
                request=request,
                db=db,
                session=session,
            )

        if query and search.columns:
            return await _run_ilike_search(
                entry=entry,
                search=search,
                query=query,
                request=request,
                db=db,
                session=session,
                primary_key_column=primary_key_column,
            )

        return await _run_unfiltered_search(
            entry=entry,
            search=search,
            request=request,
            db=db,
            session=session,
            primary_key_column=primary_key_column,
        )


# -------------------------------------------------------------------
# Search-mode runners.
#
# Each branch builds + runs its own statement, then shapes rows
# through the link builder.  Pulled out so :meth:`_search_resource`
# stays a thin dispatcher.  All three use single-column keyset
# pagination — pk-only by default, ts_rank for the tsvector path.
# -------------------------------------------------------------------


async def _pk_only_search(
    entry: ResourceEntry,
    request: FilterValuesRequest,
    db: AsyncSession,
) -> ValuesPage:
    """Fallback search for entries without a :class:`SearchSpec`.

    Used when a Ref points at a resource that doesn't define a
    search.  Returns ``{"value": <pk>, "label": str(<pk>)}`` rows
    paginated by pk — enough for the FE to render a typeable
    autocomplete fallback even when the consumer never wired a
    proper search.  ``q`` is ignored (no column to apply it to).
    """
    primary_key_column = getattr(entry.model, entry.pk)
    page_size = resolved_limit(request.limit)
    statement, _ = apply_keyset_pagination(
        select(primary_key_column).order_by(primary_key_column.asc()),
        entry.model,
        cursor=_decode_cursor(request.cursor),
        cursor_field=entry.pk,
        page_size=page_size,
        max_page_size=page_size,
    )
    rows = list((await db.execute(statement)).scalars().all())
    page, next_cursor = _finalise_scalar_page(rows, page_size)
    return ValuesPage(
        results=[
            {"value": str(value), "label": str(value)} for value in page
        ],
        next_cursor=next_cursor,
    )


async def _run_unfiltered_search(
    *,
    entry: ResourceEntry,
    search: SearchSpec,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
    primary_key_column: ColumnElement[Any],
) -> ValuesPage:
    """No query: ORDER BY pk; pk-keyset cursor."""
    page_size = resolved_limit(request.limit)
    statement, _ = apply_keyset_pagination(
        select(entry.model).order_by(primary_key_column.asc()),
        entry.model,
        cursor=_decode_cursor(request.cursor),
        cursor_field=entry.pk,
        page_size=page_size,
        max_page_size=page_size,
    )
    rows = list((await db.execute(statement)).scalars().all())
    page, next_cursor = _finalise_pk_page(rows, entry.pk, page_size)
    items = await _shape_link_items(page, search, session)
    return ValuesPage(results=items, next_cursor=next_cursor)


async def _run_ilike_search(
    *,
    entry: ResourceEntry,
    search: SearchSpec,
    query: str,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
    primary_key_column: ColumnElement[Any],
) -> ValuesPage:
    """Query + ILIKE: WHERE matches; ORDER BY pk; pk-keyset cursor."""
    columns = [getattr(entry.model, name) for name in search.columns]
    page_size = resolved_limit(request.limit)
    statement, _ = apply_keyset_pagination(
        select(entry.model)
        .where(or_(*[column.ilike(f"%{query}%") for column in columns]))
        .order_by(primary_key_column.asc()),
        entry.model,
        cursor=_decode_cursor(request.cursor),
        cursor_field=entry.pk,
        page_size=page_size,
        max_page_size=page_size,
    )
    rows = list((await db.execute(statement)).scalars().all())
    page, next_cursor = _finalise_pk_page(rows, entry.pk, page_size)
    items = await _shape_link_items(page, search, session)
    return ValuesPage(results=items, next_cursor=next_cursor)


async def _run_tsvector_search(
    *,
    entry: ResourceEntry,
    search: SearchSpec,
    query: str,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
) -> ValuesPage:
    """Query + tsvector: ORDER BY ts_rank DESC; rank-only cursor.

    Single-column ordering — ts_rank as the lone key.  Float
    rounding can introduce ties; in practice ranks for distinct
    matches diverge enough that the rare cursor edge case
    (skipping a tied row at the page boundary) is acceptable for
    autocomplete UX.
    """
    vector_column_name = search.vector_column

    if vector_column_name is None:  # pragma: no cover -- guarded by caller
        msg = "Tsvector runner invoked without a vector_column."
        raise ValueError(msg)

    vector = getattr(entry.model, vector_column_name)
    tsquery = func.websearch_to_tsquery("english", query)
    rank_expression = func.ts_rank(vector, tsquery).label("_rank")
    page_size = resolved_limit(request.limit)

    statement: Select[Any] = (
        select(entry.model, rank_expression)
        .where(vector.op("@@")(tsquery))
        .order_by(rank_expression.desc())
        .limit(page_size + 1)
    )
    previous_rank = _decode_rank_cursor(request.cursor)

    if previous_rank is not None:
        statement = statement.where(rank_expression < previous_rank)

    raw_rows = (await db.execute(statement)).all()
    rows = [(row[0], float(row[1])) for row in raw_rows]
    page, next_cursor = _finalise_rank_page(rows, page_size)
    instances = [model_instance for model_instance, _ in page]
    items = await _shape_link_items(instances, search, session)
    return ValuesPage(results=items, next_cursor=next_cursor)


async def _shape_link_items(
    instances: Sequence[Any],
    search: SearchSpec,
    session: Any,
) -> list[dict[str, Any]]:
    """Run the consumer link builder over each row, dump to dicts."""
    items: list[dict[str, Any]] = []

    for model_instance in instances:
        link = await search.link(model_instance, session)
        items.append(
            link.model_dump() if hasattr(link, "model_dump") else link
        )

    return items


# -------------------------------------------------------------------
# Free-text + enum value providers.
# -------------------------------------------------------------------


async def _free_text_values(
    entry: ResourceEntry,
    spec: FreeText,
    request: FilterValuesRequest,
    db: AsyncSession,
) -> ValuesPage:
    """Distinct-column ILIKE search; single-column keyset on the column."""
    column_name = spec.column or spec.name
    column = getattr(entry.model, column_name)
    query = request.q
    page_size = resolved_limit(request.limit)
    statement = select(column).distinct().order_by(column.asc())

    if query:
        statement = statement.where(column.ilike(f"%{query}%"))

    statement, _ = apply_keyset_pagination(
        statement,
        entry.model,
        cursor=_decode_cursor(request.cursor),
        cursor_field=column_name,
        page_size=page_size,
        max_page_size=page_size,
    )

    rows = list((await db.execute(statement)).scalars().all())
    page, next_cursor = _finalise_scalar_page(rows, page_size)
    return ValuesPage(
        results=[{"value": row, "label": row} for row in page],
        next_cursor=next_cursor,
    )


@dataclass(frozen=True)
class _ChoiceRow:
    """Plain dataclass row for the enum-values ``VALUES`` clause.

    :class:`Choice` is the public Pydantic type (lives on the
    discovery payload); ``values_table`` introspects dataclass
    fields, so the SQL path uses this lightweight shadow.
    """

    value: str
    label: str


async def _enum_values(
    enum_class: type[_enum_mod.Enum],
    request: FilterValuesRequest,
    db: AsyncSession,
) -> ValuesPage:
    """Enum search via Postgres ``VALUES`` — same pipeline as SQL tables.

    Builds a ``VALUES`` selectable from the enum's members and
    runs the same single-column keyset / ILIKE machinery against
    it.  Result: enum search composes with everything else
    (pagination, ordering, future filtering extensions) without
    a parallel in-memory code path.
    """
    table = values_table(
        _ChoiceRow,
        [
            _ChoiceRow(value=str(member.value), label=member.name)
            for member in enum_class
        ],
        name="enum_values",
    )
    label_column = table.c.label
    value_column = table.c.value
    query = request.q
    page_size = resolved_limit(request.limit)

    statement = select(value_column, label_column).order_by(label_column.asc())

    if query:
        statement = statement.where(label_column.ilike(f"%{query}%"))

    previous_label = _decode_cursor(request.cursor)

    if previous_label is not None:
        statement = statement.where(label_column > previous_label)

    statement = statement.limit(page_size + 1)

    rows = (await db.execute(statement)).all()
    page = list(rows[:page_size])
    has_more = len(rows) > page_size
    next_cursor = (
        _encode_cursor(str(page[-1].label)) if has_more and page else None
    )
    return ValuesPage(
        results=[{"value": row.value, "label": row.label} for row in page],
        next_cursor=next_cursor,
    )


# -------------------------------------------------------------------
# Cursor + page-finalise helpers.
#
# Two cursor formats — single-column (``"k:<value>"``) for the
# common case and rank (``"r:<float>"``) for the ts_rank ordering
# whose ordering key isn't a string.
# -------------------------------------------------------------------


_CURSOR_PREFIX = "k:"
_RANK_CURSOR_PREFIX = "r:"


def _encode_cursor(value: str) -> str:
    """Encode the column value as a single-key keyset cursor."""
    return f"{_CURSOR_PREFIX}{value}"


def _decode_cursor(cursor: str | None) -> str | None:
    """Strip the cursor tag; return ``None`` for empty/wrong-tag cursors."""
    if not cursor or not cursor.startswith(_CURSOR_PREFIX):
        return None

    return cursor.removeprefix(_CURSOR_PREFIX) or None


def _encode_rank_cursor(rank: float) -> str:
    """Encode a ts_rank value as a keyset cursor."""
    return f"{_RANK_CURSOR_PREFIX}{rank!r}"


def _decode_rank_cursor(cursor: str | None) -> float | None:
    """Decode ``r:<rank>`` into a float; ``None`` when missing/invalid."""
    if not cursor or not cursor.startswith(_RANK_CURSOR_PREFIX):
        return None

    raw = cursor.removeprefix(_RANK_CURSOR_PREFIX)

    try:
        return float(raw)

    except ValueError:
        return None


def _finalise_pk_page(
    rows: list[Any],
    pk_attr: str,
    page_size: int,
) -> tuple[list[Any], str | None]:
    """Trim the over-fetch row, build pk-only cursor for an ORM-row page."""
    has_more = len(rows) > page_size
    page = rows[:page_size]

    if not has_more or not page:
        return page, None

    last_pk = getattr(page[-1], pk_attr)
    return page, _encode_cursor(str(last_pk))


def _finalise_scalar_page(
    rows: list[Any],
    page_size: int,
) -> tuple[list[Any], str | None]:
    """Trim + build single-column cursor for a scalar-row page."""
    has_more = len(rows) > page_size
    page = rows[:page_size]

    if not has_more or not page:
        return page, None

    return page, _encode_cursor(str(page[-1]))


def _finalise_rank_page(
    rows: list[tuple[Any, float]],
    page_size: int,
) -> tuple[list[tuple[Any, float]], str | None]:
    """Trim + build rank cursor for a tsvector-ranked page."""
    has_more = len(rows) > page_size
    page = rows[:page_size]

    if not has_more or not page:
        return page, None

    _, last_rank = page[-1]
    return page, _encode_rank_cursor(last_rank)
