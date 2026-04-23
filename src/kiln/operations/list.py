"""List operation: GET / -- list all resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import (
    EnumClass,
    ExtensionSchema,
    RouteHandler,
    RouteParam,
    TestCase,
)
from kiln.config.schema import FieldSpec  # noqa: TC001
from kiln.operations._list_config import (  # noqa: TC001
    FilterConfig,
    OrderConfig,
    PaginateConfig,
)
from kiln.operations._shared import (
    _construct_response_schema,
    _construct_serializer,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.outputs import SerializerFn
    from kiln.config.schema import OperationConfig, ResourceConfig


@operation("list", scope="operation", dispatch_on="name", requires=["get"])
class List:
    """GET / -- list all resources."""

    class Options(BaseModel):
        """Options for the list operation."""

        fields: list[FieldSpec]
        filters: FilterConfig | None = None
        ordering: OrderConfig | None = None
        pagination: PaginateConfig | None = None

    def build(
        self,
        ctx: BuildContext[OperationConfig],
        options: Options,
    ) -> Iterable[object]:
        """Produce output for GET /.

        Also emits a POST /search route when any of ``filters``,
        ``ordering``, or ``pagination`` is configured.  The search
        route uses ingot's apply_filters / apply_ordering /
        apply_*_pagination helpers to build the query, and carries
        its own request/response schemas.

        Args:
            ctx: Build context for the ``"list"`` operation entry.
            options: Parsed ``Options``.

        Yields:
            The ``{Model}ListItem`` schema, its serializer, the
            GET route handler, and a test case.  When extensions
            are configured, additionally yields extension schemas
            (filter / sort / search-request / page), the POST
            ``/search`` handler, and its test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
        pk_name = getattr(resource, "pk", "id")
        schema = _construct_response_schema(
            model, options.fields, suffix="ListItem"
        )
        serializer = _construct_serializer(model, schema, stem="list_item")
        yield schema
        yield serializer

        yield RouteHandler(
            method="GET",
            path="/",
            function_name=f"list_{model.lower}s",
            response_model=f"list[{schema.name}]",
            serializer_fn=serializer.function_name,
            return_type=f"list[{schema.name}]",
            doc=f"List all {model.pascal} records.",
            body_template="fastapi/ops/list.py.j2",
            body_context={
                "extra_params": [],
                "query_modifiers": [],
                "result_expression": None,
            },
            extra_imports=[("sqlalchemy", "select")],
        )

        yield TestCase(
            op_name="list",
            method="get",
            path="/",
            status_success=200,
            is_list_response=True,
        )

        if (
            options.filters is None
            and options.ordering is None
            and options.pagination is None
        ):
            return

        yield from _build_search(
            model=model,
            pk_name=pk_name,
            options=options,
            list_item_schema=schema.name,
            serializer=serializer,
        )


def _build_search(
    *,
    model: Name,
    pk_name: str,
    options: List.Options,
    list_item_schema: str,
    serializer: SerializerFn,
) -> Iterable[object]:
    """Emit extension schemas and the POST /search handler.

    Shared logic split out of :meth:`List.build` to keep the
    primary build flow readable.
    """
    filters = options.filters
    ordering = options.ordering
    pagination = options.pagination
    pagination_mode = pagination.mode if pagination is not None else None

    if filters is not None:
        yield from _filter_schemas(
            model=model,
            filters=filters,
            field_names=[f.name for f in options.fields],
        )

    if ordering is not None:
        yield from _sort_schemas(model=model, ordering=ordering)

    search_request_name = model.suffixed("SearchRequest")
    yield ExtensionSchema(
        name=search_request_name,
        body_template="fastapi/schema_parts/search_request.py.j2",
        body_context={
            "model_name": model.pascal,
            "has_filter": filters is not None,
            "has_sort": ordering is not None,
            "pagination_mode": pagination_mode,
            "default_page_size": (
                pagination.default_page_size if pagination is not None else 20
            ),
        },
    )

    response_model = f"list[{list_item_schema}]"
    return_type = response_model
    if pagination_mode is not None:
        page_name = model.suffixed("Page")
        yield ExtensionSchema(
            name=page_name,
            body_template="fastapi/schema_parts/page.py.j2",
            body_context={
                "model_name": model.pascal,
                "item_type": list_item_schema,
                "mode": pagination_mode,
            },
        )
        response_model = page_name
        return_type = page_name

    default_sort_field = ordering.default if ordering is not None else None
    default_sort_dir = ordering.default_dir if ordering is not None else "asc"
    max_page_size = pagination.max_page_size if pagination is not None else 100
    cursor_field = (
        pagination.cursor_field if pagination is not None else pk_name
    )

    yield RouteHandler(
        method="POST",
        path="/search",
        function_name=f"search_{model.lower}s",
        params=[RouteParam(name="body", annotation=search_request_name)],
        response_model=response_model,
        return_type=return_type,
        serializer_fn=serializer.function_name,
        request_schema=search_request_name,
        doc=f"Search {model.pascal} records.",
        body_template="fastapi/ops/search.py.j2",
        body_context={
            "has_filter": filters is not None,
            "has_sort": ordering is not None,
            "pagination_mode": pagination_mode,
            "default_sort_field": default_sort_field or pk_name,
            "default_sort_dir": default_sort_dir,
            "max_page_size": max_page_size,
            "cursor_field": cursor_field,
        },
        extra_imports=_search_runtime_imports(
            has_filter=filters is not None,
            has_sort=ordering is not None,
            pagination_mode=pagination_mode,
        ),
    )

    yield TestCase(
        op_name="search",
        method="post",
        path="/search",
        status_success=200,
        has_request_body=True,
        request_schema=search_request_name,
    )


def _filter_schemas(
    *,
    model: Name,
    filters: FilterConfig,
    field_names: list[str],
) -> Iterable[object]:
    """Emit FilterCondition and FilterExpression schemas."""
    allowed = filters.fields or field_names
    yield ExtensionSchema(
        name=model.suffixed("FilterCondition"),
        body_template="fastapi/schema_parts/filter_node.py.j2",
        body_context={
            "model_name": model.pascal,
            "allowed_fields": allowed,
        },
        extra_imports=[
            ("typing", "Any"),
            ("typing", "Literal"),
            ("pydantic", "ConfigDict"),
            ("pydantic", "Field"),
        ],
    )


def _sort_schemas(
    *,
    model: Name,
    ordering: OrderConfig,
) -> Iterable[object]:
    """Emit SortField enum and SortClause schema."""
    yield EnumClass(
        name=model.suffixed("SortField"),
        members=[(f.upper(), f) for f in ordering.fields],
        base="str, Enum",
    )
    yield ExtensionSchema(
        name=model.suffixed("SortClause"),
        body_template="fastapi/schema_parts/sort_clause.py.j2",
        body_context={"model_name": model.pascal},
        extra_imports=[("typing", "Literal")],
    )


def _search_runtime_imports(
    *,
    has_filter: bool,
    has_sort: bool,
    pagination_mode: str | None,
) -> list[tuple[str, str]]:
    """Return (module, name) import pairs for the search handler."""
    pairs: list[tuple[str, str]] = [("sqlalchemy", "select")]
    if has_filter:
        pairs.append(("ingot", "apply_filters"))
    if has_sort:
        pairs.append(("ingot", "apply_ordering"))
    if pagination_mode == "keyset":
        pairs.append(("ingot", "apply_keyset_pagination"))
    elif pagination_mode == "offset":
        pairs.append(("ingot", "apply_offset_pagination"))
    return pairs
