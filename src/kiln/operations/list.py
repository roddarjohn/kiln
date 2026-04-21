"""List operation: GET / -- list all resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
from kiln.operations._shared import _read_schema_outputs
from kiln.renderers.fastapi import (
    FASTAPI_REGISTRY,
    FASTAPI_TAGS,
    build_handler_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx


@dataclass
class ListRoute(RouteHandler):
    """Route handler emitted by the :class:`List` operation."""

    op_name: str = "list"


@operation("list", scope="resource", requires=["get"])
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
        ctx: BuildContext,
        options: Options,
    ) -> Iterable[object]:
        """Produce output for GET /.

        Args:
            ctx: Build context with resource config.
            options: Parsed ``Options``.

        Yields:
            The ``{Model}ListItem`` schema, its serializer, the
            route handler, and a test case.

        """
        _, model = Name.from_dotted(ctx.instance.model)
        schema, serializer = _read_schema_outputs(
            model, options.fields, "ListItem", "list_item"
        )
        yield schema
        yield serializer

        yield ListRoute(
            method="GET",
            path="/",
            function_name=f"list_{model.lower}s",
            response_model=f"list[{schema.name}]",
            serializer_fn=serializer.function_name,
            return_type=f"list[{schema.name}]",
            doc=f"List all {model.pascal} records.",
        )

        yield TestCase(
            op_name="list",
            method="get",
            path="/",
            status_success=200,
            is_list_response=True,
        )


@FASTAPI_REGISTRY.renders(ListRoute, tags=FASTAPI_TAGS)
def _render(h: ListRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        h,
        ctx,
        body_template="fastapi/ops/list.py.j2",
        body_extra={
            "http_method": "get",
            "route_path": "/",
            "response_model": h.response_model,
            "return_type": h.return_type,
            "serializer_fn": h.serializer_fn,
            "extra_params": [],
            "query_modifiers": [],
            "result_expression": None,
        },
        sql_verb="select",
        needs_utils=False,
    )
