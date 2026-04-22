"""List operation: GET / -- list all resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, TestCase
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

        Args:
            ctx: Build context for the ``"list"`` operation entry.
            options: Parsed ``Options``.

        Yields:
            The ``{Model}ListItem`` schema, its serializer, the
            route handler, and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
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
                "http_method": "get",
                "route_path": "/",
                "response_model": f"list[{schema.name}]",
                "return_type": f"list[{schema.name}]",
                "serializer_fn": serializer.function_name,
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
