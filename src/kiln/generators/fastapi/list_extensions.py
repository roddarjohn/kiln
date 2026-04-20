"""List-operation extensions: filtering, ordering, pagination.

These are helper functions called by
:class:`~kiln.generators.fastapi.operations.ListOperation`
when the corresponding config keys are present.  They deposit
contributions into the ``list_extensions`` dict on the route
spec's context.

All three are optional — when absent the list operation
generates a bare ``select(Model)`` query with a GET endpoint.

When filtering is enabled the list endpoint becomes a
``POST /search`` that accepts a JSON body with a recursive
AND/OR filter tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from kiln.config.schema import FieldSpec, FieldType  # noqa: TC001
from kiln.generators._env import render_snippet
from kiln.generators._helpers import PYTHON_TYPES
from kiln_core.naming import prefix_import

if TYPE_CHECKING:
    from kiln.generators.fastapi.operations import SharedContext
    from kiln_core import FileSpec

# -------------------------------------------------------------------
# Operator defaults per field type
# -------------------------------------------------------------------

DEFAULT_OPERATORS: dict[FieldType, list[str]] = {
    "str": ["eq", "neq", "contains", "starts_with", "in"],
    "email": [
        "eq",
        "neq",
        "contains",
        "starts_with",
        "in",
    ],
    "int": ["eq", "neq", "gt", "gte", "lt", "lte", "in"],
    "float": [
        "eq",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
    ],
    "bool": ["eq"],
    "uuid": ["eq", "in"],
    "datetime": ["eq", "gt", "gte", "lt", "lte"],
    "date": ["eq", "gt", "gte", "lt", "lte"],
    "json": [],
}


# -------------------------------------------------------------------
# Config models
# -------------------------------------------------------------------


class FilterConfig(BaseModel):
    """Configuration for list filtering.

    When ``fields`` is omitted or empty, all fields from the
    list operation's ``fields`` config are used.  Otherwise
    only the named fields are filterable.
    """

    fields: list[str] | None = None


class OrderConfig(BaseModel):
    """Configuration for list ordering."""

    fields: list[str]
    default: str | None = None
    default_dir: Literal["asc", "desc"] = "asc"


class PaginateConfig(BaseModel):
    """Configuration for list pagination."""

    mode: Literal["keyset", "offset"] = "keyset"
    cursor_field: str = "id"
    cursor_type: FieldType = "uuid"
    max_page_size: int = 100
    default_page_size: int = 20


# -------------------------------------------------------------------
# Filtering helper
# -------------------------------------------------------------------


def contribute_filters(
    specs: dict[str, FileSpec],
    ctx: SharedContext,
    config: FilterConfig,
    list_fields: list[FieldSpec] | None,
) -> None:
    """Mark the list as POST /search and add the filter modifier.

    Generates per-model ``FilterCondition`` and
    ``FilterExpression`` Pydantic models with typed field
    names.  The ``{Model}SearchRequest`` schema is rendered
    later by :func:`contribute_search_request`.

    Args:
        specs: Mutable dict of file specs.
        ctx: Shared context for this resource.
        config: The filter configuration.
        list_fields: The fields from the list operation.

    """
    schema = specs["schema"]
    route = specs["route"]
    ext = route.context["list_extensions"]

    # Determine allowed filterable fields
    if config.fields:
        allowed = config.fields
    elif list_fields:
        allowed = [f.name for f in list_fields]
    else:
        allowed = []

    # Schema: render FilterCondition + FilterExpression
    snippet = render_snippet(
        "fastapi/schema_parts/filter_node.py.j2",
        model_name=ctx.model.pascal,
        allowed_fields=allowed,
    )
    schema.context["schema_classes"].append(snippet)
    schema.imports.add_from("typing", "Any", "Literal")
    schema.imports.add_from("pydantic", "ConfigDict", "Field")

    # Route: import apply_filters from utils
    utils_module = prefix_import(ctx.package_prefix, "utils")
    route.imports.add_from(utils_module, "apply_filters")

    # Mark route as POST /search
    ext["http_method"] = "post"
    ext["route_path"] = "/search"

    # Extension contributions
    ext["extra_params"].append(f"body: {ctx.model.suffixed('SearchRequest')},")
    ext["query_modifiers"].append(
        "if body.filter is not None:\n"
        "        stmt = apply_filters("
        f"stmt, body.filter, {ctx.model.pascal})"
    )


# -------------------------------------------------------------------
# Ordering helper
# -------------------------------------------------------------------


def contribute_ordering(
    specs: dict[str, FileSpec],
    ctx: SharedContext,
    config: OrderConfig,
) -> None:
    """Deposit sort enum, sort clause, and ordering modifier.

    Generates ``{Model}SortField`` enum and
    ``{Model}SortClause`` model.  When filters are enabled,
    ordering is read from ``body.sort`` and applied via
    :func:`apply_ordering`; otherwise it uses query parameters.

    Args:
        specs: Mutable dict of file specs.
        ctx: Shared context for this resource.
        config: The ordering configuration.

    """
    schema = specs["schema"]
    route = specs["route"]
    ext = route.context["list_extensions"]

    # Schema: render SortField enum
    sort_fields = [{"name": f, "value": f} for f in config.fields]
    snippet = render_snippet(
        "fastapi/schema_parts/sort_field.py.j2",
        model_name=ctx.model.pascal,
        sort_fields=sort_fields,
    )
    schema.context["schema_classes"].append(snippet)
    schema.imports.add_from("enum", "Enum")

    has_search_body = ext.get("http_method") == "post"
    default_col = config.default or ctx.pk_name

    if has_search_body:
        # Schema: render SortClause model
        clause_snippet = render_snippet(
            "fastapi/schema_parts/sort_clause.py.j2",
            model_name=ctx.model.pascal,
        )
        schema.context["schema_classes"].append(clause_snippet)
        schema.imports.add_from("typing", "Literal")

        # Route: import apply_ordering from utils
        utils_module = prefix_import(ctx.package_prefix, "utils")
        route.imports.add_from(utils_module, "apply_ordering")

        ext["query_modifiers"].append(
            "stmt = apply_ordering("
            f"stmt, body.sort, {ctx.model.pascal},"
            f' "{default_col}", "{config.default_dir}")'
        )
    else:
        # Ordering via query parameters (single column)
        sort_field_cls = ctx.model.suffixed("SortField")
        route.imports.add_from("typing", "Literal")
        ext["extra_params"].append(f"sort_by: {sort_field_cls} | None = None,")
        ext["extra_params"].append(
            f'sort_dir: Literal["asc", "desc"] = "{config.default_dir}",'
        )
        modifier_lines = [
            "sort_col = (",
            f"    getattr({ctx.model.pascal}, sort_by.value)",
            "    if sort_by",
            f"    else {ctx.model.pascal}.{default_col}",
            ")",
            'if sort_dir == "desc":',
            "    stmt = stmt.order_by(sort_col.desc())",
            "else:",
            "    stmt = stmt.order_by(sort_col.asc())",
        ]
        ext["query_modifiers"].extend(modifier_lines)


# -------------------------------------------------------------------
# Pagination helper
# -------------------------------------------------------------------


def contribute_pagination(
    specs: dict[str, FileSpec],
    ctx: SharedContext,
    config: PaginateConfig,
) -> None:
    """Deposit page schema, params, and result expression.

    Supports two modes:

    - ``keyset``: Cursor-based pagination using a monotonic
      column (typically the primary key).
    - ``offset``: Traditional LIMIT/OFFSET with a total count.

    Args:
        specs: Mutable dict of file specs.
        ctx: Shared context for this resource.
        config: The pagination configuration.

    """
    if config.mode == "keyset":
        _contribute_keyset(specs, ctx, config)
    else:
        _contribute_offset(specs, ctx, config)


def _contribute_keyset(
    specs: dict[str, FileSpec],
    ctx: SharedContext,
    config: PaginateConfig,
) -> None:
    """Keyset (cursor-based) pagination."""
    schema = specs["schema"]
    route = specs["route"]
    ext = route.context["list_extensions"]

    item_type = ctx.response_schema if ctx.has_resource_schema else "dict"

    # Schema: Page model
    snippet = render_snippet(
        "fastapi/schema_parts/page.py.j2",
        model_name=ctx.model.pascal,
        item_type=item_type,
        mode="keyset",
    )
    schema.context["schema_classes"].append(snippet)
    schema.exports.append(ctx.model.suffixed("Page"))

    page_cls = ctx.model.suffixed("Page")
    ext["response_model"] = page_cls
    ext["return_type"] = page_cls

    has_search_body = ext.get("http_method") == "post"

    # Route: import apply_keyset_pagination from utils
    utils_module = prefix_import(ctx.package_prefix, "utils")
    route.imports.add_from(utils_module, "apply_keyset_pagination")

    if not has_search_body:
        route.imports.add_from("fastapi", "Query")
        ext["extra_params"].append("cursor: str | None = None,")
        ext["extra_params"].append(
            f"page_size: Annotated[int, Query("
            f"ge=1, le={config.max_page_size}"
            f")] = {config.default_page_size},"
        )

    # Build cursor cast expression
    cursor_py_type = PYTHON_TYPES[config.cursor_type]
    cursor_field = config.cursor_field
    if config.cursor_type == "uuid":
        route.imports.add("uuid")
        cast_expr = "uuid.UUID(raw_cursor)"
    elif config.cursor_type in ("int", "float"):
        cast_expr = f"{cursor_py_type}(raw_cursor)"
    else:
        cast_expr = "raw_cursor"

    raw_cursor = "body.cursor" if has_search_body else "cursor"
    page_size_src = "body.page_size" if has_search_body else "page_size"

    # Result expression (pagination + execute + return)
    result_lines = [
        f"raw_cursor = {raw_cursor}",
        f"cursor_val = {cast_expr} if raw_cursor else None",
        "stmt, page_size = apply_keyset_pagination(",
        "    stmt,",
        f"    {ctx.model.pascal},",
        "    cursor_val,",
        f'    "{cursor_field}",',
        f"    {page_size_src},",
        f"    {config.max_page_size},",
        ")",
        "result = await db.execute(stmt)",
        "rows = list(result.scalars())",
        "has_more = len(rows) > page_size",
        "items = rows[:page_size]",
    ]
    if ctx.has_resource_schema:
        result_lines.append(f"return {page_cls}(")
        result_lines.append(
            f"    items=[to_{ctx.model.lower}_resource(obj) for obj in items],"
        )
    else:
        result_lines.append(f"return {page_cls}(")
        result_lines.append("    items=items,")
    next_cur = f"str(items[-1].{cursor_field}) if has_more and items else None"
    result_lines.extend(
        [
            f"    next_cursor=({next_cur}),",
            ")",
        ]
    )
    ext["result_expression"] = "\n    ".join(result_lines)


def _contribute_offset(
    specs: dict[str, FileSpec],
    ctx: SharedContext,
    config: PaginateConfig,
) -> None:
    """Traditional offset pagination."""
    schema = specs["schema"]
    route = specs["route"]
    ext = route.context["list_extensions"]

    item_type = ctx.response_schema if ctx.has_resource_schema else "dict"

    # Schema: Page model
    snippet = render_snippet(
        "fastapi/schema_parts/page.py.j2",
        model_name=ctx.model.pascal,
        item_type=item_type,
        mode="offset",
    )
    schema.context["schema_classes"].append(snippet)
    schema.exports.append(ctx.model.suffixed("Page"))

    page_cls = ctx.model.suffixed("Page")
    ext["response_model"] = page_cls
    ext["return_type"] = page_cls

    has_search_body = ext.get("http_method") == "post"

    # Route: import apply_offset_pagination from utils
    utils_module = prefix_import(ctx.package_prefix, "utils")
    route.imports.add_from(utils_module, "apply_offset_pagination")

    if not has_search_body:
        route.imports.add_from("fastapi", "Query")
        ext["extra_params"].append("offset: Annotated[int, Query(ge=0)] = 0,")
        ext["extra_params"].append(
            f"limit: Annotated[int, Query("
            f"ge=1, le={config.max_page_size}"
            f")] = {config.default_page_size},"
        )

    offset_var = "body.offset" if has_search_body else "offset"
    limit_var = "body.limit" if has_search_body else "limit"

    # Result expression (pagination + return)
    result_lines = [
        "total, rows = await apply_offset_pagination(",
        "    db,",
        "    stmt,",
        f"    {offset_var},",
        f"    {limit_var},",
        f"    {config.max_page_size},",
        ")",
    ]
    if ctx.has_resource_schema:
        result_lines.append(f"return {page_cls}(")
        result_lines.append(
            f"    items=[to_{ctx.model.lower}_resource(obj) for obj in rows],"
        )
    else:
        result_lines.append(f"return {page_cls}(")
        result_lines.append("    items=rows,")
    result_lines.extend(
        [
            "    total=total,",
            ")",
        ]
    )
    ext["result_expression"] = "\n    ".join(result_lines)


# -------------------------------------------------------------------
# Search request schema (rendered after all extensions)
# -------------------------------------------------------------------


def contribute_search_request(
    specs: dict[str, FileSpec],
    ctx: SharedContext,
    ordering: OrderConfig | None,
    pagination: PaginateConfig | None,
) -> None:
    """Render the ``{Model}SearchRequest`` body schema.

    Must be called after :func:`contribute_filters`,
    :func:`contribute_ordering`, and
    :func:`contribute_pagination` so it can include sort and
    pagination fields on the request body.

    Args:
        specs: Mutable dict of file specs.
        ctx: Shared context for this resource.
        ordering: Ordering config, or ``None``.
        pagination: Pagination config, or ``None``.

    """
    schema = specs["schema"]

    has_sort = ordering is not None
    pagination_mode = pagination.mode if pagination else None
    default_page_size = pagination.default_page_size if pagination else 20

    snippet = render_snippet(
        "fastapi/schema_parts/search_request.py.j2",
        model_name=ctx.model.pascal,
        sort_fields=has_sort,
        pagination_mode=pagination_mode,
        default_page_size=default_page_size,
    )
    schema.context["schema_classes"].append(snippet)
    schema.exports.append(ctx.model.suffixed("SearchRequest"))
