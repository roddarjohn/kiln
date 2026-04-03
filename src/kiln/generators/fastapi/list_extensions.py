"""List-operation extensions: filtering, ordering, pagination.

These are helper classes called by
:class:`~kiln.generators.fastapi.operations.ListOperation`
when the corresponding config keys are present.  They deposit
contributions into the ``list_extensions`` dict on the route
spec's context.

All three are optional — when absent the list operation
generates a bare ``select(Model)`` query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from kiln.config.schema import FieldSpec, FieldType  # noqa: TC001
from kiln.generators._env import render_snippet
from kiln.generators._helpers import PYTHON_TYPES, prefix_import

if TYPE_CHECKING:
    from kiln.generators.base import FileSpec
    from kiln.generators.fastapi.operations import SharedContext

# -------------------------------------------------------------------
# Operator defaults per field type
# -------------------------------------------------------------------

DEFAULT_OPERATORS: dict[FieldType, list[str]] = {
    "str": ["eq", "neq", "contains", "starts_with", "in"],
    "email": ["eq", "neq", "contains", "starts_with", "in"],
    "int": ["eq", "neq", "gt", "gte", "lt", "lte", "in"],
    "float": ["eq", "neq", "gt", "gte", "lt", "lte", "in"],
    "bool": ["eq"],
    "uuid": ["eq", "in"],
    "datetime": ["eq", "gt", "gte", "lt", "lte"],
    "date": ["eq", "gt", "gte", "lt", "lte"],
    "json": [],
}

#: Python type strings for ``list[T]`` used by the ``in`` operator.
_LIST_TYPES: dict[str, str] = {
    "uuid": "list[uuid.UUID]",
    "str": "list[str]",
    "email": "list[str]",
    "int": "list[int]",
    "float": "list[float]",
    "bool": "list[bool]",
    "datetime": "list[datetime]",
    "date": "list[date]",
}


def _filter_py_type(field_type: FieldType, operator: str) -> str:
    """Return the Python type annotation for a filter parameter."""
    if operator == "in":
        return _LIST_TYPES.get(field_type, f"list[{PYTHON_TYPES[field_type]}]")
    return PYTHON_TYPES[field_type]


def _add_filter_type_imports(
    imports: ImportCollector,
    field_type: FieldType,
) -> None:
    """Add type-specific imports for a single filter parameter."""
    if field_type == "uuid":
        imports.add("uuid")
    elif field_type == "datetime":
        imports.add_from("datetime", "datetime")
    elif field_type == "date":
        imports.add_from("datetime", "date")
    elif field_type == "json":
        imports.add_from("typing", "Any")


if TYPE_CHECKING:
    from kiln.generators._helpers import ImportCollector


# -------------------------------------------------------------------
# Config models
# -------------------------------------------------------------------


class FilterFieldSpec(BaseModel):
    """A field available for filtering, with optional operator list."""

    name: str
    type: FieldType
    operators: list[str] | None = None


class FilterConfig(BaseModel):
    """Configuration for list filtering."""

    fields: list[FilterFieldSpec]


class OrderConfig(BaseModel):
    """Configuration for list ordering."""

    fields: list[FieldSpec]
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
) -> None:
    """Deposit filter schema and query modifier.

    Generates a ``{Model}ListFilter`` Pydantic schema and adds
    a call to the generic ``apply_filters()`` utility as a query
    modifier.

    Args:
        specs: Mutable dict of file specs.
        ctx: Shared context for this resource.
        config: The filter configuration.

    """
    schema = specs["schema"]
    route = specs["route"]
    ext = route.context["list_extensions"]

    filter_fields: list[dict[str, str]] = []
    for field in config.fields:
        ops = field.operators or DEFAULT_OPERATORS.get(field.type, [])
        for op in ops:
            param_name = f"{field.name}_{op}" if op != "eq" else field.name
            py_type = _filter_py_type(field.type, op)
            filter_fields.append(
                {
                    "param_name": param_name,
                    "py_type": py_type,
                    "column": field.name,
                    "operator": op,
                }
            )
            _add_filter_type_imports(schema.imports, field.type)

    # Schema: render ListFilter model
    snippet = render_snippet(
        "fastapi/schema_parts/list_filter.py.j2",
        model_name=ctx.model.pascal,
        filter_fields=filter_fields,
    )
    schema.context["schema_classes"].append(snippet)
    schema.exports.append(ctx.model.suffixed("ListFilter"))

    # Route: import apply_filters from utils
    utils_module = prefix_import(ctx.package_prefix, "utils")
    route.imports.add_from(utils_module, "apply_filters")

    # Extension contributions
    ext["extra_params"].append(
        f"filters: Annotated[{ctx.model.suffixed('ListFilter')}, Depends()],"
    )
    ext["query_modifiers"].append(
        f"stmt = apply_filters(stmt, filters, {ctx.model.pascal})"
    )


# -------------------------------------------------------------------
# Ordering helper
# -------------------------------------------------------------------


def contribute_ordering(
    specs: dict[str, FileSpec],
    ctx: SharedContext,
    config: OrderConfig,
) -> None:
    """Deposit sort enum, params, and order_by modifier.

    Generates a ``{Model}SortField`` string enum and adds
    ``sort_by`` / ``sort_dir`` query parameters to the list
    handler.

    Args:
        specs: Mutable dict of file specs.
        ctx: Shared context for this resource.
        config: The ordering configuration.

    """
    schema = specs["schema"]
    route = specs["route"]
    ext = route.context["list_extensions"]

    # Schema: render SortField enum
    sort_fields = [{"name": f.name, "value": f.name} for f in config.fields]
    snippet = render_snippet(
        "fastapi/schema_parts/sort_field.py.j2",
        model_name=ctx.model.pascal,
        sort_fields=sort_fields,
    )
    schema.context["schema_classes"].append(snippet)
    schema.exports.append(ctx.model.suffixed("SortField"))
    schema.imports.add_from("enum", "Enum")

    # Route imports
    route.imports.add_from("typing", "Literal")

    # Extension contributions: params
    sort_field_cls = ctx.model.suffixed("SortField")
    ext["extra_params"].append(f"sort_by: {sort_field_cls} | None = None,")
    ext["extra_params"].append(
        f'sort_dir: Literal["asc", "desc"] = "{config.default_dir}",'
    )

    # Extension contributions: query modifier
    default_col = config.default or ctx.pk_name
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

    # Response model override
    ext["response_model"] = page_cls
    ext["return_type"] = page_cls

    # Params — use Query() for validation
    route.imports.add_from("fastapi", "Query")
    ext["extra_params"].append("cursor: str | None = None,")
    ext["extra_params"].append(
        f"page_size: Annotated[int, Query("
        f"ge=1, le={config.max_page_size}"
        f")] = {config.default_page_size},"
    )

    # Query modifiers
    cursor_py_type = PYTHON_TYPES[config.cursor_type]
    cursor_field = config.cursor_field
    if config.cursor_type == "uuid":
        route.imports.add("uuid")
        cast_expr = "uuid.UUID(cursor)"
    elif config.cursor_type in ("int", "float"):
        cast_expr = f"{cursor_py_type}(cursor)"
    else:
        cast_expr = "cursor"

    ext["query_modifiers"].extend(
        [
            "if cursor:",
            "    stmt = stmt.where(",
            f"        {ctx.model.pascal}.{cursor_field} > {cast_expr}",
            "    )",
            "stmt = stmt.limit(page_size + 1)",
        ]
    )

    # Result expression
    result_lines = [
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
    result_lines.extend(
        [
            "    next_cursor=(",
            f"        str(items[-1].{cursor_field})",
            "        if has_more and items",
            "        else None",
            "    ),",
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

    # Response model override
    ext["response_model"] = page_cls
    ext["return_type"] = page_cls

    # Params — use Query() for validation
    route.imports.add_from("fastapi", "Query")
    ext["extra_params"].append("offset: Annotated[int, Query(ge=0)] = 0,")
    ext["extra_params"].append(
        f"limit: Annotated[int, Query("
        f"ge=1, le={config.max_page_size}"
        f")] = {config.default_page_size},"
    )

    # Query modifiers
    route.imports.add_from("sqlalchemy", "func")

    # Result expression
    result_lines = [
        "count_result = await db.execute(",
        "    stmt.with_only_columns(func.count())",
        ")",
        "total = count_result.scalar_one()",
        "result = await db.execute(",
        "    stmt.offset(offset).limit(limit)",
        ")",
        "rows = list(result.scalars())",
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
