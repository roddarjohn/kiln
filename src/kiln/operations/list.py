"""List operation: POST /search -- list/filter/sort/paginate resources."""

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
    from kiln.config.schema import OperationConfig, ResourceConfig


@operation("list", scope="operation", dispatch_on="name", requires=["get"])
class List:
    """POST /search -- list/filter/sort/paginate resources.

    Always emits a single ``POST /search`` route; never a GET.
    When ``filters``, ``ordering``, or ``pagination`` is configured,
    a ``SearchRequest`` body carries the query, the handler calls
    the matching ingot helpers, and (for pagination) a ``Page``
    schema wraps the response.  With no extensions configured,
    the handler takes no body and returns ``list[{Model}ListItem]``.
    """

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
        """Produce output for POST /search.

        Args:
            ctx: Build context for the ``"list"`` operation entry.
            options: Parsed ``Options``.

        Yields:
            The ``{Model}ListItem`` schema, its serializer, and the
            POST ``/search`` handler + test case.  Additionally
            yields extension schemas (filter / sort / search-request
            / page) when the corresponding options are configured.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
        pk_name = getattr(resource, "pk", "id")
        list_item_schema = _construct_response_schema(
            model, options.fields, suffix="ListItem"
        )
        serializer = _construct_serializer(
            model, list_item_schema, stem="list_item"
        )
        yield list_item_schema
        yield serializer

        filters = options.filters
        ordering = options.ordering
        pagination = options.pagination
        pagination_mode = pagination.mode if pagination is not None else None
        has_extensions = (
            filters is not None
            or ordering is not None
            or pagination is not None
        )

        if filters is not None:
            yield from _filter_schemas(
                model=model,
                filters=filters,
                field_names=[f.name for f in options.fields],
            )

        if ordering is not None:
            yield from _sort_schemas(model=model, ordering=ordering)

        search_request_name: str | None = None
        if has_extensions:
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
                        pagination.default_page_size
                        if pagination is not None
                        else 20
                    ),
                },
            )

        response_model = f"list[{list_item_schema.name}]"
        if pagination_mode is not None:
            page_name = model.suffixed("Page")
            yield ExtensionSchema(
                name=page_name,
                body_template="fastapi/schema_parts/page.py.j2",
                body_context={
                    "model_name": model.pascal,
                    "item_type": list_item_schema.name,
                    "mode": pagination_mode,
                },
            )
            response_model = page_name

        default_sort_field = ordering.default if ordering is not None else None
        default_sort_dir = (
            ordering.default_dir if ordering is not None else "asc"
        )
        max_page_size = (
            pagination.max_page_size if pagination is not None else 100
        )
        cursor_field = (
            pagination.cursor_field if pagination is not None else pk_name
        )

        params = (
            [RouteParam(name="body", annotation=search_request_name)]
            if search_request_name is not None
            else []
        )

        yield RouteHandler(
            method="POST",
            path="/search",
            function_name=f"list_{model.lower}s",
            params=params,
            response_model=response_model,
            return_type=response_model,
            serializer_fn=serializer.function_name,
            request_schema=search_request_name,
            doc=f"List {model.pascal} records.",
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
            op_name="list",
            method="post",
            path="/search",
            status_success=200,
            has_request_body=has_extensions,
            request_schema=search_request_name,
            is_list_response=pagination_mode is None,
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
