"""List operation: POST /search -- bare list endpoint.

Emits an always-present ``POST /search`` route plus the schemas
and serializer that any list op needs.  The Filter / Order /
Paginate extension ops run after List (via ``requires=["list"]``)
and amend the ``SearchRequest`` schema and the ``RouteHandler``
that this module emits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from kiln.config.schema import FieldSpec  # noqa: TC001
from kiln.operations.types import (
    RouteHandler,
    RouteParam,
    SchemaClass,
    TestCase,
    _construct_response_schema,
    _construct_serializer,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import OperationConfig, ResourceConfig


@operation("list", scope="operation", dispatch_on="name", requires=["get"])
class List:
    """POST /search -- list resources.

    Always emits:

    * ``{Model}ListItem`` response schema + matching serializer.
    * ``{Model}SearchRequest`` request schema (empty unless an
      extension op — Filter / Order / Paginate — fills it in).
    * ``POST /search`` route handler and its test case.

    Extension ops run after this one (they declare
    ``requires=["list"]``) and amend the SearchRequest + handler
    in place.
    """

    class Options(BaseModel):
        """Options for the list operation."""

        fields: list[FieldSpec]

    def build(
        self,
        ctx: BuildContext[OperationConfig],
        options: Options,
    ) -> Iterable[object]:
        """Emit the list schemas, serializer, handler, and test case.

        Args:
            ctx: Build context for the ``"list"`` op entry.
            options: Parsed ``Options`` (just the field list).

        Yields:
            ListItem schema, serializer, SearchRequest schema,
            search RouteHandler, and TestCase.

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

        search_request_name = model.suffixed("SearchRequest")
        yield SchemaClass(
            name=search_request_name,
            body_template="fastapi/schema_parts/search_request.py.j2",
            body_context={
                "model_name": model.pascal,
                "has_filter": False,
                "has_sort": False,
                "pagination_mode": None,
                "default_page_size": 20,
            },
        )

        response_model = f"list[{list_item_schema.name}]"
        yield RouteHandler(
            method="POST",
            path="/search",
            function_name=f"list_{model.lower}s",
            params=[
                RouteParam(name="body", annotation=search_request_name),
            ],
            response_model=response_model,
            return_type=response_model,
            serializer_fn=serializer.function_name,
            request_schema=search_request_name,
            doc=f"List {model.pascal} records.",
            body_template="fastapi/ops/search.py.j2",
            body_context={
                "has_filter": False,
                "has_sort": False,
                "pagination_mode": None,
                "default_sort_field": pk_name,
                "default_sort_dir": "asc",
                "max_page_size": 100,
                "cursor_field": pk_name,
            },
            extra_imports=[("sqlalchemy", "select")],
        )

        yield TestCase(
            op_name="list",
            method="post",
            path="/search",
            status_success=200,
            has_request_body=True,
            request_schema=search_request_name,
            is_list_response=True,
        )
