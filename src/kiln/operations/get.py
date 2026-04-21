"""Get operation: GET /{pk} -- retrieve a single resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, RouteParam, TestCase
from kiln.generators._helpers import PYTHON_TYPES
from kiln.operations._shared import FieldsOptions, _read_schema_outputs
from kiln.renderers.fastapi import (
    FASTAPI_REGISTRY,
    FASTAPI_TAGS,
    _response_schema_name,
    build_handler_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx


@dataclass
class GetRoute(RouteHandler):
    """Route handler emitted by the :class:`Get` operation."""

    op_name: str = "get"


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
        resource = ctx.instance
        _, model = Name.from_dotted(resource.model)
        pk_name = resource.pk
        pk_py_type = PYTHON_TYPES[resource.pk_type]

        schema, serializer = _read_schema_outputs(
            model, options.fields, "Resource", "resource"
        )

        yield schema
        yield serializer

        handler = GetRoute(
            method="GET",
            path=f"/{{{pk_name}}}",
            function_name=f"get_{model.lower}",
            response_model=schema.name,
            serializer_fn=serializer.function_name,
            return_type=schema.name,
            doc=f"Get a {model.pascal} by {pk_name}.",
        )
        handler.params.append(RouteParam(name=pk_name, annotation=pk_py_type))
        yield handler

        yield TestCase(
            op_name="get",
            method="get",
            path=f"/{{{pk_name}}}",
            status_success=200,
            status_not_found=404,
            response_schema=schema.name,
        )


@FASTAPI_REGISTRY.renders(GetRoute, tags=FASTAPI_TAGS)
def _render(h: GetRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        h,
        ctx,
        body_template="fastapi/ops/get.py.j2",
        body_extra={
            "response_schema": _response_schema_name(h),
            "serializer_fn": h.serializer_fn,
        },
        sql_verb="select",
        needs_utils=True,
    )
