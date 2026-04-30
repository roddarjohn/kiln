"""Project-wide resource registry: discovery + value-provider engine.

Generated codegen emits one :class:`ResourceRegistry` per project,
populated declaratively with one :class:`ResourceEntry` per
resource.  Today the registry covers filter discovery and
value-provider dispatch; future work folds in actions, dump
schemas, and other resource-scoped concerns under the same map
(see ``ResourceEntry``'s extension points).

Project-wide route handlers (``GET /_filters``,
``GET /_filters/{resource}``, ``GET /_filters/{resource}/{field}``,
``POST /_values/{resource}``, ``POST /_values/{resource}/{field}``)
delegate everything to :meth:`ResourceRegistry.discovery` and
:meth:`ResourceRegistry.values` — they hold no logic of their own.

Pagination is keyset (cursor on the primary ordering column) when
no search query is present; with a query, results are reranked so
starts-with-q matches come first, and pagination falls back to an
offset cursor under that compound ordering.  Cursor strings are
tagged with a one-character prefix (``"k:"`` keyset, ``"o:"``
offset) so the registry decodes either mode without ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select

from ingot.filter_values import (
    FilterValuesRequest,
    enum_values,
    resolved_limit,
)

if TYPE_CHECKING:
    import enum
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select


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
# Resource-level search + entry
# -------------------------------------------------------------------


@dataclass(frozen=True)
class SearchSpec:
    """Resource-level ``POST /_values/{resource}`` search configuration.

    Two search modes; pick whichever matches the resource:

    - **ILIKE fallback** (no ``vector_column``): ``columns`` are
      OR'd via ILIKE on the search query, and results are reranked
      so starts-with matches come first.
    - **tsvector mode** (``vector_column`` set): the named column
      is matched via ``@@ websearch_to_tsquery(q)`` and ranked via
      ``ts_rank(...)``.  Pairs with the pgcraft-generated
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
# Registry
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

    def discovery(
        self,
        *,
        resource: str | None = None,
        field: str | None = None,
    ) -> dict[str, Any]:
        """Return the discovery payload, narrowed by *resource* / *field*.

        With both unset, returns the full project payload.  With
        *resource*, returns just that resource's payload.  With
        both, returns the single field's descriptor; raises
        :class:`fastapi.HTTPException` (404) when the field or
        resource is unknown.

        Args:
            resource: Resource slug to narrow to.  ``None`` returns
                the full project payload.
            field: Field name within *resource* to narrow to.
                Requires *resource* to be set.

        Returns:
            JSON-serializable payload.

        """
        if resource is None:
            return {
                "resources": {
                    key: self._resource_payload(key) for key in self._entries
                },
            }

        entry = self._require_entry(resource)

        if field is None:
            return self._resource_payload(resource)

        for spec in entry.fields:
            if spec.name == field:
                return self._field_payload(resource, spec)

        raise HTTPException(
            status_code=404,
            detail=f"Unknown filter field: {field}",
        )

    def _resource_payload(self, resource: str) -> dict[str, Any]:
        """Build the per-resource discovery dict for *resource*."""
        entry = self._require_entry(resource)
        payload: dict[str, Any] = {
            "resource": resource,
            "filters": [
                self._field_payload(resource, spec) for spec in entry.fields
            ],
        }

        if entry.search is not None:
            payload["search"] = {"endpoint": f"/_values/{resource}"}

        return payload

    def _field_payload(
        self,
        resource: str,
        spec: FilterField,
    ) -> dict[str, Any]:
        """Build the discovery dict for one field on *resource*."""
        values: dict[str, Any] = {"kind": spec.kind}

        if isinstance(spec, Enum):
            values["choices"] = [
                {"value": m.value, "label": m.name} for m in spec.enum_class
            ]
            values["endpoint"] = f"/_values/{resource}/{spec.name}"

        elif isinstance(spec, FreeText):
            values["endpoint"] = f"/_values/{resource}/{spec.name}"

        elif isinstance(spec, Ref):
            values["type"] = spec.target

            if self._has_search(spec.target):
                values["endpoint"] = f"/_values/{spec.target}"

        elif isinstance(spec, SelfRef):
            values["type"] = spec.type

            if self._has_search(resource):
                values["endpoint"] = f"/_values/{resource}"

        elif isinstance(spec, LiteralField):
            values["type"] = spec.type

        # Bool: nothing extra to attach.

        return {
            "field": spec.name,
            "operators": list(spec.operators),
            "values": values,
        }

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
    ) -> dict[str, Any]:
        """Dispatch a value-provider request to the right code path.

        ``field=None`` runs the resource-level search (requires
        :attr:`ResourceEntry.search`); a named field dispatches by
        :class:`FilterField` variant — ``Enum`` and ``FreeText``
        serve their own values, ``Ref`` and ``SelfRef`` recurse
        into the target resource's search, ``Bool`` and
        ``LiteralField`` 404.

        Args:
            resource: Registered resource slug.
            field: Field name on that resource, or ``None`` for the
                resource-level search.
            request: Parsed :class:`FilterValuesRequest` body.
            db: Async SQLAlchemy session.
            session: Auth session forwarded to link builders;
                ``None`` when auth isn't configured.

        Returns:
            ``{"results": [...], "next_cursor": ...}``.

        Raises:
            fastapi.HTTPException: 404 on unknown resource/field
                or on a field that has no values endpoint.

        """
        entry = self._require_entry(resource)

        if field is None:
            return await self._search_resource(entry, request, db, session)

        spec = next((f for f in entry.fields if f.name == field), None)

        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown filter field: {field}",
            )

        if isinstance(spec, Enum):
            return enum_values(spec.enum_class, request)

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
    ) -> dict[str, Any]:
        """Distinct-column ILIKE search with relevance ordering."""
        col_name = spec.column or spec.name
        col = getattr(entry.model, col_name)
        stmt = select(col).distinct()

        q = request.q

        if q:
            stmt = stmt.where(col.ilike(f"%{q}%"))

        stmt = stmt.order_by(*_relevance_ordering(q, [col]), col)

        rows, next_cursor = await _paginate_scalars(
            stmt=stmt,
            primary_col=col,
            request=request,
            db=db,
            relevance_active=bool(q),
        )

        return {
            "results": [{"value": r, "label": r} for r in rows],
            "next_cursor": next_cursor,
        }

    async def _search_resource(
        self,
        entry: ResourceEntry,
        request: FilterValuesRequest,
        db: AsyncSession,
        session: Any,
    ) -> dict[str, Any]:
        """Resource-level search; shape rows via the link builder.

        Two query modes:

        * **tsvector** (``search.vector_column`` set): ``vec @@
          websearch_to_tsquery(q)`` for the match, ``ts_rank(...)``
          for the order — true relevance from the pgcraft column.
        * **ILIKE** (default): OR of ``ILIKE %q%`` over
          :attr:`SearchSpec.columns`, with a starts-with relevance
          bucket placed first.

        Both modes still tiebreak by primary key, and both use
        offset pagination when a query is present (compound
        ordering is hard to keyset cleanly) and keyset on the pk
        when no query is set.
        """
        if entry.search is None:
            raise HTTPException(
                status_code=404,
                detail="Resource has no search endpoint",
            )

        search = entry.search
        pk_col = getattr(entry.model, entry.pk)
        stmt = select(entry.model)
        q = request.q

        if q and search.vector_column is not None:
            stmt = _apply_tsvector_search(
                stmt, entry.model, search.vector_column, q, pk_col
            )

        elif q and search.columns:
            cols = [getattr(entry.model, c) for c in search.columns]
            stmt = stmt.where(or_(*[c.ilike(f"%{q}%") for c in cols]))
            stmt = stmt.order_by(*_relevance_ordering(q, cols), pk_col)

        else:
            stmt = stmt.order_by(pk_col)

        rows, next_cursor = await _paginate_objects(
            stmt=stmt,
            model=entry.model,
            pk_attr=entry.pk,
            request=request,
            db=db,
            relevance_active=bool(q),
        )

        items: list[Any] = []

        for obj in rows:
            link = await search.link(obj, session)
            items.append(
                link.model_dump() if hasattr(link, "model_dump") else link
            )

        return {"results": items, "next_cursor": next_cursor}


# -------------------------------------------------------------------
# Pagination + cursor helpers.
#
# Two cursor modes exist because the ordering changes when ``q`` is
# set: relevance bucket + column is hard to keyset cleanly, so we
# fall back to offset under the compound ordering.  The one-char
# prefix on the cursor string disambiguates the two on decode.
# -------------------------------------------------------------------


_OFFSET_PREFIX = "o:"
_KEYSET_PREFIX = "k:"


def _relevance_ordering(
    q: str | None,
    cols: Sequence[ColumnElement[Any]],
) -> tuple[ColumnElement[Any], ...]:
    """Build a starts-with-first ORDER BY prefix when ``q`` is set."""
    if not q or not cols:
        return ()

    prefix = f"{q}%"
    starts_with = or_(*[c.ilike(prefix) for c in cols])
    # 0 → starts-with bucket, 1 → everything else; ascending sort
    # surfaces the bucket first.
    return (case((starts_with, 0), else_=1).asc(),)


def _apply_tsvector_search(
    stmt: Select[Any],
    model: type,
    vector_column: str,
    q: str,
    pk_col: ColumnElement[Any],
) -> Select[Any]:
    """Apply a Postgres tsvector match + ts_rank ordering.

    ``websearch_to_tsquery`` parses common search-engine syntax
    (quoted phrases, ``-exclusion``) so the consumer doesn't have
    to hand-craft a tsquery — the same ``q`` the FE sends to the
    ILIKE path works here too.

    Tied ranks are broken by primary key for stable pagination.
    """
    vec = getattr(model, vector_column)
    query = func.websearch_to_tsquery("english", q)
    rank = func.ts_rank(vec, query)
    return stmt.where(vec.op("@@")(query)).order_by(rank.desc(), pk_col)


async def _paginate_scalars(
    *,
    stmt: Select[Any],
    primary_col: ColumnElement[Any],
    request: FilterValuesRequest,
    db: AsyncSession,
    relevance_active: bool,
) -> tuple[list[Any], str | None]:
    """Execute *stmt* under the right pagination mode, returning scalars."""
    limit = resolved_limit(request.limit)
    paginated = _apply_pagination(
        stmt=stmt,
        primary_col=primary_col,
        cursor=request.cursor,
        limit=limit,
        relevance_active=relevance_active,
    )
    rows = list((await db.execute(paginated.stmt)).scalars().all())
    return paginated.finalize(rows, key=lambda v: v)


async def _paginate_objects(
    *,
    stmt: Select[Any],
    model: type,
    pk_attr: str,
    request: FilterValuesRequest,
    db: AsyncSession,
    relevance_active: bool,
) -> tuple[list[Any], str | None]:
    """Execute *stmt* under the right pagination mode, returning objects."""
    limit = resolved_limit(request.limit)
    pk_col = getattr(model, pk_attr)
    paginated = _apply_pagination(
        stmt=stmt,
        primary_col=pk_col,
        cursor=request.cursor,
        limit=limit,
        relevance_active=relevance_active,
    )
    rows = list((await db.execute(paginated.stmt)).scalars().all())
    return paginated.finalize(rows, key=lambda obj: getattr(obj, pk_attr))


@dataclass
class _Pagination:
    """Internal handle returned by :func:`_apply_pagination`."""

    stmt: Select[Any]
    limit: int
    relevance_active: bool
    last_offset: int

    def finalize(
        self,
        rows: list[Any],
        key: Callable[[Any], Any],
    ) -> tuple[list[Any], str | None]:
        """Trim the over-fetched row, build ``next_cursor``."""
        has_more = len(rows) > self.limit
        page = rows[: self.limit]

        if not has_more or not page:
            return page, None

        if self.relevance_active:
            return page, _encode_offset(self.last_offset + self.limit)

        return page, _encode_keyset(str(key(page[-1])))


def _apply_pagination(
    *,
    stmt: Select[Any],
    primary_col: ColumnElement[Any],
    cursor: str | None,
    limit: int,
    relevance_active: bool,
) -> _Pagination:
    """Apply offset (relevance-active) or keyset (no-q) windowing."""
    if relevance_active:
        offset = _decode_offset(cursor)
        return _Pagination(
            stmt=stmt.offset(offset).limit(limit + 1),
            limit=limit,
            relevance_active=True,
            last_offset=offset,
        )

    prev = _decode_keyset(cursor)

    if prev is not None:
        stmt = stmt.where(primary_col > prev)

    return _Pagination(
        stmt=stmt.limit(limit + 1),
        limit=limit,
        relevance_active=False,
        last_offset=0,
    )


def _decode_offset(cursor: str | None) -> int:
    """Return the offset encoded in *cursor*, or 0 when absent/invalid."""
    if not cursor:
        return 0

    raw = cursor.removeprefix(_OFFSET_PREFIX)

    try:
        return max(0, int(raw))

    except ValueError:
        return 0


def _encode_offset(value: int) -> str:
    """Encode *value* as an offset cursor."""
    return f"{_OFFSET_PREFIX}{value}"


def _decode_keyset(cursor: str | None) -> str | None:
    """Strip the keyset prefix; return ``None`` for empty cursors."""
    if not cursor:
        return None

    return cursor.removeprefix(_KEYSET_PREFIX)


def _encode_keyset(value: str) -> str:
    """Encode *value* as a keyset cursor."""
    return f"{_KEYSET_PREFIX}{value}"
