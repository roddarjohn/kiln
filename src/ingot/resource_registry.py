"""Project-wide resource registry: discovery + value-provider engine.

Generated codegen emits one :class:`ResourceRegistry` per project,
populated declaratively with one :class:`ResourceEntry` per
resource.  Today the registry covers filter discovery and
value-provider dispatch; future work folds in actions, dump
schemas, and other resource-scoped concerns under the same map.

Project-wide route handlers (``GET /_filters``,
``GET /_filters/{resource}``, ``GET /_filters/{resource}/{field}``,
``POST /_values/{resource}``, ``POST /_values/{resource}/{field}``)
delegate everything to :meth:`ResourceRegistry.discovery` and
:meth:`ResourceRegistry.values` — they hold no logic of their own.

Both endpoints return typed Pydantic models.  Discovery is a
discriminated union (``kind``) so the FE-side OpenAPI client narrows
on field shape automatically.  Values are returned as
:class:`ValuesPage` carrying a list of dicts plus an optional
``next_cursor``.

Pagination is always keyset.  Three cursor modes carry enough state
to resume any of the three ordering shapes the registry uses:

* ``"k:<pk>"`` — no query.  ORDER BY pk ASC.
* ``"b:<bucket>:<pk>"`` — query + ILIKE relevance.  ORDER BY
  bucket ASC, pk ASC, where bucket is ``0`` for starts-with hits
  and ``1`` otherwise.
* ``"r:<rank>:<pk>"`` — query + tsvector.  ORDER BY ts_rank DESC,
  pk ASC.

The one-character tag on the cursor disambiguates decode without
requiring callers to track mode out-of-band.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select

from ingot.filter_values import (
    FilterValuesRequest,
    enum_values,
    resolved_limit,
)
from ingot.pagination import (
    SortDirection,
    apply_compound_keyset_pagination,
    apply_keyset_pagination,
)

if TYPE_CHECKING:
    import enum
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession


# -------------------------------------------------------------------
# Field specs.  One frozen dataclass per ``FilterValueKind`` from
# :mod:`be.config.schema`; the union :data:`FilterField` keeps callers
# from having to import the leaves individually.
# -------------------------------------------------------------------


@dataclass(frozen=True)
class Enum:
    """Enum-typed filter field.

    Discovery emits ``{value, label}`` pairs from ``enum_class``
    (computed at request time so additions to the enum surface
    without a regen).  Values endpoint serves the same list,
    ``q``-filterable.
    """

    name: str
    enum_class: type[enum.Enum]
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
    """FK to another resource; values delegate to that resource's search.

    ``target`` is the registry key of the target resource.  The
    target must declare a :class:`SearchSpec`; the FE hits
    ``POST /_values/{target}`` and the registry routes through.
    """

    name: str
    target: str
    operators: tuple[str, ...] = ("eq", "in")
    kind: Literal["ref"] = "ref"


@dataclass(frozen=True)
class SelfRef:
    """Filter on this resource's own primary key.

    Discovery emits ``{"kind": "self", "type": <slug>}`` so the FE
    renders an autocomplete tied to this resource (when it has a
    :class:`SearchSpec`) or a plain typed input otherwise.
    """

    name: str
    type: str
    operators: tuple[str, ...] = ("eq", "in")
    kind: Literal["self"] = "self"


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


FilterField = Enum | FreeText | Ref | SelfRef | LiteralField | Bool
"""Sum of every supported filter-field shape."""


# -------------------------------------------------------------------
# Typed discovery payload models.
#
# All discovery endpoints return a Pydantic model — never a plain
# dict — so the FE-side OpenAPI client gets a real schema and so
# FastAPI's ``response_model=`` machinery serialises consistently.
# The ``ValuesDescriptor`` union is discriminated on ``kind`` so
# clients can narrow on field shape without runtime type checks.
# -------------------------------------------------------------------


class Choice(BaseModel):
    """One ``{value, label}`` pair in an enum field's discovery payload."""

    value: Any
    """Original enum value (whatever ``enum.value`` returns —
    typically ``str`` for :class:`enum.StrEnum`, ``int`` for plain
    enums)."""

    label: str
    """Enum member name, used as the human-readable label."""


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

    ``endpoint`` is ``None`` when the target resource has no
    :class:`SearchSpec` configured — the FE falls back to a typed
    input in that case.
    """

    kind: Literal["ref"] = "ref"
    type: str
    endpoint: str | None = None


class SelfValuesDescriptor(BaseModel):
    """Discovery descriptor for a :class:`SelfRef` field."""

    kind: Literal["self"] = "self"
    type: str
    endpoint: str | None = None


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
    | SelfValuesDescriptor
    | LiteralValuesDescriptor
    | BoolValuesDescriptor,
    Field(discriminator="kind"),
]
"""Discriminated union of every per-field discovery descriptor."""


class FieldDiscovery(BaseModel):
    """Discovery payload for one filterable field on a resource."""

    field: str
    operators: list[str]
    values: ValuesDescriptor


class SearchDiscovery(BaseModel):
    """Discovery descriptor for the resource-level search endpoint."""

    endpoint: str


class ResourceDiscovery(BaseModel):
    """Discovery payload for one resource's filters + search."""

    resource: str
    filters: list[FieldDiscovery]
    search: SearchDiscovery | None = None


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
#
# Both filter endpoints are POST so they can accept structured
# narrowing parameters in the request body — the FE picks which
# resources or fields it cares about, and the registry returns just
# those.  The codegen layer typically narrows ``resources`` to a
# ``Literal`` over the registered slugs for tighter FE typing.
# -------------------------------------------------------------------


class FilterDiscoveryRequest(BaseModel):
    """Request body for ``POST /_filters``.

    ``resources`` selects which resources appear in the response:

    * ``None`` (the default) — every registered resource (full
      project discovery).
    * empty list — none (returns ``{"resources": []}``).
    * one or more slugs — that subset, in request order.
    """

    resources: list[str] | None = None


class FieldRef(BaseModel):
    """A pointer to one filter field on one resource."""

    resource: str
    field: str


class FieldDiscoveryRequest(BaseModel):
    """Request body for ``POST /_filters/fields``.

    ``fields`` is a non-empty list of ``(resource, field)``
    references; the response carries one :class:`FieldDiscovery`
    per ref, in the same order.
    """

    fields: list[FieldRef] = Field(default_factory=list)


# -------------------------------------------------------------------
# Typed values-page model.
#
# Per-row shape varies (``{value, label}`` for enum/free-text,
# link payload for resource search) but all of them are dicts post-
# serialisation, so the response carries ``list[dict[str, Any]]``.
# -------------------------------------------------------------------


class ValuesPage(BaseModel):
    """Response shape for value-provider endpoints.

    ``results`` is ``[{"value": ..., "label": ...}]`` for
    enum/free-text fields and the consumer's link-payload shape
    (already ``model_dump``-ed) for resource search and ref dispatch.
    """

    results: list[dict[str, Any]]
    next_cursor: str | None = None


# -------------------------------------------------------------------
# Resource-level search + entry.
# -------------------------------------------------------------------


@dataclass(frozen=True)
class SearchSpec:
    """Resource-level ``POST /_values/{resource}`` search configuration.

    Two search modes; pick whichever matches the resource:

    - **ILIKE fallback** (no ``vector_column``): ``columns`` are
      OR'd via ILIKE on the search query, and results are reranked
      so starts-with matches come first.
    - **tsvector mode** (``vector_column`` set): the named column
      is matched via ``@@ websearch_to_tsquery(query)`` and ranked
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

        ``request.resources`` is the gating filter:

        * ``None`` — every registered resource (full project payload).
        * empty list — empty payload (``{"resources": []}``).
        * one or more slugs — that subset, in request order.

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

        The response preserves the request order so the FE can
        pair the result list against its own request list by
        index.  Unknown resource/field combinations raise
        :class:`fastapi.HTTPException` (404).
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

            resolved.append(self._field_payload(ref.resource, spec))

        return FieldsDiscovery(fields=resolved)

    def _resource_payload(self, resource: str) -> ResourceDiscovery:
        """Build the per-resource discovery payload for *resource*."""
        entry = self._require_entry(resource)
        return ResourceDiscovery(
            resource=resource,
            filters=[
                self._field_payload(resource, spec) for spec in entry.fields
            ],
            search=(
                SearchDiscovery(endpoint=f"/_values/{resource}")
                if entry.search is not None
                else None
            ),
        )

    def _field_payload(
        self,
        resource: str,
        spec: FilterField,
    ) -> FieldDiscovery:
        """Build the discovery payload for one field on *resource*."""
        return FieldDiscovery(
            field=spec.name,
            operators=list(spec.operators),
            values=self._values_descriptor(resource, spec),
        )

    def _values_descriptor(
        self,
        resource: str,
        spec: FilterField,
    ) -> ValuesDescriptor:
        """Return the ``values`` block for one field."""
        if isinstance(spec, Enum):
            return EnumValuesDescriptor(
                choices=[
                    Choice(value=member.value, label=member.name)
                    for member in spec.enum_class
                ],
                endpoint=f"/_values/{resource}/{spec.name}",
            )

        if isinstance(spec, FreeText):
            return FreeTextValuesDescriptor(
                endpoint=f"/_values/{resource}/{spec.name}",
            )

        if isinstance(spec, Ref):
            return RefValuesDescriptor(
                type=spec.target,
                endpoint=(
                    f"/_values/{spec.target}"
                    if self._has_search(spec.target)
                    else None
                ),
            )

        if isinstance(spec, SelfRef):
            return SelfValuesDescriptor(
                type=spec.type,
                endpoint=(
                    f"/_values/{resource}"
                    if self._has_search(resource)
                    else None
                ),
            )

        if isinstance(spec, LiteralField):
            return LiteralValuesDescriptor(type=spec.type)

        # Bool: nothing extra to attach.
        return BoolValuesDescriptor()

    def _has_search(self, resource: str) -> bool:
        """Whether *resource* declares a :class:`SearchSpec`."""
        entry = self._entries.get(resource)
        return entry is not None and entry.search is not None

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

        ``field=None`` runs the resource-level search (requires
        :attr:`ResourceEntry.search`); a named field dispatches by
        :class:`FilterField` variant — ``Enum`` and ``FreeText``
        serve their own values, ``Ref`` and ``SelfRef`` recurse
        into the target resource's search, ``Bool`` and
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
            return ValuesPage(**enum_values(spec.enum_class, request))

        if isinstance(spec, FreeText):
            return await self._free_text_values(entry, spec, request, db)

        if isinstance(spec, Ref):
            target_entry = self._require_entry(spec.target)
            return await self._search_resource(
                target_entry, request, db, session
            )

        if isinstance(spec, SelfRef):
            if entry.search is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Field {field!r} has no value provider",
                )

            return await self._search_resource(entry, request, db, session)

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

    async def _free_text_values(
        self,
        entry: ResourceEntry,
        spec: FreeText,
        request: FilterValuesRequest,
        db: AsyncSession,
    ) -> ValuesPage:
        """Distinct-column ILIKE search with relevance ordering.

        Single-column ordering, so the keyset cursor is either:

        * ``"k:<value>"`` when no query is set (ORDER BY column ASC).
        * ``"b:<bucket>:<value>"`` when a query is set
          (ORDER BY bucket ASC, column ASC) — compound keyset
          since the bucket column has only two distinct values.
        """
        column_name = spec.column or spec.name
        column = getattr(entry.model, column_name)
        query = request.q
        page_size = resolved_limit(request.limit)
        statement = select(column).distinct()

        if query:
            bucket = _bucket_expr(query, [column])
            ordering: list[tuple[ColumnElement[Any], SortDirection]] = [
                (bucket, "asc"),
                (column, "asc"),
            ]
            statement, _ = apply_compound_keyset_pagination(
                statement.where(column.ilike(f"%{query}%")).order_by(
                    bucket.asc(), column.asc()
                ),
                columns=ordering,
                cursor=_decode_bucket_cursor(request.cursor),
                page_size=page_size,
                max_page_size=page_size,
            )

        else:
            statement, _ = apply_keyset_pagination(
                statement.order_by(column.asc()),
                entry.model,
                cursor=_decode_pk_cursor(request.cursor),
                cursor_field=column_name,
                page_size=page_size,
                max_page_size=page_size,
            )

        rows = list((await db.execute(statement)).scalars().all())
        page, next_cursor = _finalise_scalar_page(rows, query, page_size)
        return ValuesPage(
            results=[{"value": row, "label": row} for row in page],
            next_cursor=next_cursor,
        )

    async def _search_resource(
        self,
        entry: ResourceEntry,
        request: FilterValuesRequest,
        db: AsyncSession,
        session: Any,
    ) -> ValuesPage:
        """Resource-level search; shape rows via the link builder.

        Three cursor modes — pk-only, bucket+pk, rank+pk — line up
        with the three orderings (no-q, q+ILIKE, q+tsvector).
        """
        if entry.search is None:
            raise HTTPException(
                status_code=404,
                detail="Resource has no search endpoint",
            )

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
                primary_key_column=primary_key_column,
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
# stays a thin dispatcher.
# -------------------------------------------------------------------


async def _run_unfiltered_search(
    *,
    entry: ResourceEntry,
    search: SearchSpec,
    request: FilterValuesRequest,
    db: AsyncSession,
    session: Any,
    primary_key_column: ColumnElement[Any],
) -> ValuesPage:
    """No query: ORDER BY pk ASC, keyset on pk."""
    page_size = resolved_limit(request.limit)
    statement, _ = apply_keyset_pagination(
        select(entry.model).order_by(primary_key_column.asc()),
        entry.model,
        cursor=_decode_pk_cursor(request.cursor),
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
    """Query + ILIKE: ORDER BY bucket ASC, pk ASC; compound keyset."""
    columns = [getattr(entry.model, name) for name in search.columns]
    bucket = _bucket_expr(query, columns)
    page_size = resolved_limit(request.limit)
    ordering: list[tuple[ColumnElement[Any], SortDirection]] = [
        (bucket, "asc"),
        (primary_key_column, "asc"),
    ]
    statement, _ = apply_compound_keyset_pagination(
        select(entry.model)
        .where(or_(*[column.ilike(f"%{query}%") for column in columns]))
        .order_by(bucket.asc(), primary_key_column.asc()),
        columns=ordering,
        cursor=_decode_bucket_cursor(request.cursor),
        page_size=page_size,
        max_page_size=page_size,
    )
    rows = list((await db.execute(statement)).scalars().all())
    page, next_cursor = _finalise_bucket_page(
        rows, entry.pk, search.columns, query, page_size
    )
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
    primary_key_column: ColumnElement[Any],
) -> ValuesPage:
    """Query + tsvector: ORDER BY ts_rank DESC, pk ASC; compound keyset.

    The rank is read off each row directly — :func:`select` adds it
    as a labelled column so we don't have to recompute it Python-side
    (we couldn't anyway: ``ts_rank`` is a Postgres function).
    """
    vector_column_name = search.vector_column

    if vector_column_name is None:  # pragma: no cover -- guarded by caller
        msg = "Tsvector runner invoked without a vector_column."
        raise ValueError(msg)

    vector = getattr(entry.model, vector_column_name)
    tsquery = func.websearch_to_tsquery("english", query)
    rank_expression = func.ts_rank(vector, tsquery).label("_rank")
    page_size = resolved_limit(request.limit)

    ordering: list[tuple[ColumnElement[Any], SortDirection]] = [
        (rank_expression, "desc"),
        (primary_key_column, "asc"),
    ]
    statement, _ = apply_compound_keyset_pagination(
        select(entry.model, rank_expression)
        .where(vector.op("@@")(tsquery))
        .order_by(rank_expression.desc(), primary_key_column.asc()),
        columns=ordering,
        cursor=_decode_rank_cursor(request.cursor),
        page_size=page_size,
        max_page_size=page_size,
    )
    raw_rows = (await db.execute(statement)).all()
    rows = [(row[0], float(row[1])) for row in raw_rows]
    page, next_cursor = _finalise_rank_page(rows, entry.pk, page_size)
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
        items.append(link.model_dump() if hasattr(link, "model_dump") else link)

    return items


# -------------------------------------------------------------------
# Cursor formats.
#
# Three tags carry the registry's three ordering shapes.  Decoders
# are tolerant: an empty / mismatched / unparseable cursor decodes
# to ``None``, matching the "start from the beginning" path.  WHERE
# clause construction lives in
# :func:`ingot.pagination.apply_compound_keyset_pagination`.
# -------------------------------------------------------------------


_PK_CURSOR_PREFIX = "k:"
_BUCKET_CURSOR_PREFIX = "b:"
_RANK_CURSOR_PREFIX = "r:"


def _bucket_expr(
    query: str,
    columns: Sequence[ColumnElement[Any]],
) -> ColumnElement[Any]:
    """Build the ``CASE`` expression that classifies a row's relevance.

    Returns 0 for rows where any of *columns* starts with *query*
    (case-insensitive), 1 otherwise.  Wired identically into the
    ORDER BY (so starts-with hits sort first) and the keyset
    predicate (so cursor decoding lines up).
    """
    starts_with = or_(*[column.ilike(f"{query}%") for column in columns])
    return case((starts_with, 0), else_=1)


# -------------------------------------------------------------------
. 5. # Page finalisers.
#
# After the runner executes its over-fetched (LIMIT n+1) statement
# and reads the rows, the finaliser for that mode trims the spare
# row and computes the next cursor.  Cursor formats differ by mode
# (pk vs bucket+pk vs rank+pk) so each gets its own helper.
# -------------------------------------------------------------------


def _finalise_scalar_page(
    rows: list[Any],
    query: str | None,
    requested_limit: int | None,
) -> tuple[list[Any], str | None]:
    """Trim + build the next cursor for a free-text scalar page."""
    limit = resolved_limit(requested_limit)
    has_more = len(rows) > limit
    page = rows[:limit]

    if not has_more or not page:
        return page, None

    last_value = page[-1]

    if query:
        bucket = _row_bucket_from_value(query, last_value)
        return page, _encode_bucket_cursor(bucket, str(last_value))

    return page, _encode_pk_cursor(str(last_value))


def _finalise_pk_page(
    rows: list[Any],
    pk_attr: str,
    requested_limit: int | None,
) -> tuple[list[Any], str | None]:
    """Trim + build pk-only cursor for an ORM-row page."""
    limit = resolved_limit(requested_limit)
    has_more = len(rows) > limit
    page = rows[:limit]

    if not has_more or not page:
        return page, None

    last_pk = getattr(page[-1], pk_attr)
    return page, _encode_pk_cursor(str(last_pk))


def _finalise_bucket_page(
    rows: list[Any],
    pk_attr: str,
    column_names: Sequence[str],
    query: str,
    requested_limit: int | None,
) -> tuple[list[Any], str | None]:
    """Trim + build (bucket, pk) cursor for a relevance-bucketed page."""
    limit = resolved_limit(requested_limit)
    has_more = len(rows) > limit
    page = rows[:limit]

    if not has_more or not page:
        return page, None

    last_row = page[-1]
    bucket = _row_bucket_from_columns(query, column_names, last_row)
    last_pk = getattr(last_row, pk_attr)
    return page, _encode_bucket_cursor(bucket, str(last_pk))


def _finalise_rank_page(
    rows: list[tuple[Any, float]],
    pk_attr: str,
    requested_limit: int | None,
) -> tuple[list[tuple[Any, float]], str | None]:
    """Trim + build (rank, pk) cursor for a tsvector-ranked page."""
    limit = resolved_limit(requested_limit)
    has_more = len(rows) > limit
    page = rows[:limit]

    if not has_more or not page:
        return page, None

    last_instance, last_rank = page[-1]
    last_pk = getattr(last_instance, pk_attr)
    return page, _encode_rank_cursor(last_rank, str(last_pk))


# -------------------------------------------------------------------
# Bucket extraction (Python-side).
#
# Computing the relevance bucket of a row in Python avoids adding a
# labelled CASE column to the SELECT just to read it back.  The
# tsvector path can't do this trick — ts_rank is a Postgres function
# — but the ILIKE path's logic is plain string comparison.
# -------------------------------------------------------------------


def _row_bucket_from_value(query: str, value: Any) -> int:
    """Return ``0`` when *value* starts with *query* (case-insensitive)."""
    if value is None:
        return 1

    return 0 if str(value).lower().startswith(query.lower()) else 1


def _row_bucket_from_columns(
    query: str,
    column_names: Sequence[str],
    instance: Any,
) -> int:
    """Return ``0`` when any column on *instance* starts with *query*."""
    lowered_query = query.lower()

    for name in column_names:
        attribute_value = getattr(instance, name, None)

        if attribute_value is None:
            continue

        if str(attribute_value).lower().startswith(lowered_query):
            return 0

    return 1


# -------------------------------------------------------------------
# Cursor encode / decode.
#
# Three formats, one-character tag.  Decoders are tolerant: an
# empty / mismatched / unparseable cursor returns ``None`` and the
# caller treats it as "no cursor" (start from the beginning).
# -------------------------------------------------------------------


def _encode_pk_cursor(value: str) -> str:
    """Encode a single-column keyset cursor."""
    return f"{_PK_CURSOR_PREFIX}{value}"


def _decode_pk_cursor(cursor: str | None) -> str | None:
    """Strip the pk tag; ``None`` for empty / wrong-tag cursors."""
    if not cursor or not cursor.startswith(_PK_CURSOR_PREFIX):
        return None

    return cursor.removeprefix(_PK_CURSOR_PREFIX) or None


def _encode_bucket_cursor(bucket: int, primary_key: str) -> str:
    """Encode a (bucket, pk) cursor for ILIKE-relevance ordering."""
    return f"{_BUCKET_CURSOR_PREFIX}{bucket}:{primary_key}"


def _decode_bucket_cursor(
    cursor: str | None,
) -> tuple[int, str] | None:
    """Decode ``b:<bucket>:<pk>`` into ``(bucket, pk)``."""
    if not cursor or not cursor.startswith(_BUCKET_CURSOR_PREFIX):
        return None

    body = cursor.removeprefix(_BUCKET_CURSOR_PREFIX)
    bucket_str, separator, primary_key = body.partition(":")

    if not separator or not primary_key:
        return None

    try:
        bucket = int(bucket_str)

    except ValueError:
        return None

    return bucket, primary_key


def _encode_rank_cursor(rank: float, primary_key: str) -> str:
    """Encode a (rank, pk) cursor for ts_rank ordering."""
    return f"{_RANK_CURSOR_PREFIX}{rank!r}:{primary_key}"


def _decode_rank_cursor(
    cursor: str | None,
) -> tuple[float, str] | None:
    """Decode ``r:<rank>:<pk>`` into ``(rank, pk)``."""
    if not cursor or not cursor.startswith(_RANK_CURSOR_PREFIX):
        return None

    body = cursor.removeprefix(_RANK_CURSOR_PREFIX)
    rank_str, separator, primary_key = body.partition(":")

    if not separator or not primary_key:
        return None

    try:
        rank = float(rank_str)

    except ValueError:
        return None

    return rank, primary_key
