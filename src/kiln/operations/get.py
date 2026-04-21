"""Get operation: GET /{pk} -- retrieve a single resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, RouteParam, TestCase
from kiln.generators._helpers import PYTHON_TYPES
from kiln.operations._shared import (
    FieldsOptions,
    _construct_response_schema,
    _construct_serializer,
)
from kiln.renderers import registry
from kiln.renderers.fastapi import (
    _response_schema_name,
    build_handler_fragment,
    utils_imports,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx


@dataclass
class GetRoute(RouteHandler):
    """Route handler emitted by the :class:`Get` operation."""


@operation("get", scope="resource")
class Get:
    """GET /{pk} -- retrieve a single resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext,
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for GET /{pk}.

        Args:
            ctx: Build context with resource config.
            options: Parsed :class:`FieldsOptions`.

        Yields:
            The ``{Model}Resource`` schema, its serializer, the
            route handler, and a test case.

        """
        _, model = Name.from_dotted(ctx.instance.model)

        schema = _construct_response_schema(model, options.fields, "Resource")
        serializer = _construct_serializer(model, schema, "resource")

        yield schema
        yield serializer

        yield GetRoute(
            method="GET",
            path=f"/{{{ctx.instance.pk}}}",
            function_name=f"get_{model.lower}",
            params=[
                RouteParam(
                    name=ctx.instance.pk,
                    annotation=PYTHON_TYPES[ctx.instance.pk_type],
                )
            ],
            response_model=schema.name,
            serializer_fn=serializer.function_name,
            return_type=schema.name,
            doc=f"Get a {model.pascal} by {ctx.instance.pk}.",
        )

        yield TestCase(
            op_name="get",
            method="get",
            path=f"/{{{ctx.instance.pk}}}",
            status_success=200,
            status_not_found=404,
            response_schema=schema.name,
        )


@registry.renders(GetRoute)
def _render(handler: GetRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        handler,
        ctx,
        body_template="fastapi/ops/get.py.j2",
        body_extra={
            "response_schema": _response_schema_name(handler),
            "serializer_fn": handler.serializer_fn,
        },
        extra_imports=[("sqlalchemy", "select"), *utils_imports(ctx)],
    )
