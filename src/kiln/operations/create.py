"""Create operation: POST / -- create a new resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, SchemaClass, TestCase
from kiln.operations._shared import FieldsOptions, _field_dicts
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
class CreateRoute(RouteHandler):
    """Route handler emitted by the :class:`Create` operation."""

    op_name: str = "create"


@operation("create", scope="resource", requires=["list"])
class Create:
    """POST / -- create a new resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext,
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for POST /.

        Args:
            ctx: Build context with resource config.
            options: Parsed :class:`FieldsOptions`.

        Yields:
            The ``{Model}CreateRequest`` schema, the route handler,
            and a test case.

        """
        _, model = Name.from_dotted(ctx.instance.model)
        request_schema = model.suffixed("CreateRequest")

        yield SchemaClass(
            name=request_schema,
            fields=_field_dicts(options.fields),
            doc=f"Request body for creating a {model.pascal}.",
        )

        yield CreateRoute(
            method="POST",
            path="/",
            function_name=f"create_{model.lower}",
            status_code=201,
            doc=f"Create a new {model.pascal}.",
            request_schema=request_schema,
        )

        yield TestCase(
            op_name="create",
            method="post",
            path="/",
            status_success=201,
            status_invalid=422,
            has_request_body=True,
            request_schema=request_schema,
        )


@FASTAPI_REGISTRY.renders(CreateRoute, tags=FASTAPI_TAGS)
def _render(h: CreateRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        h,
        ctx,
        body_template="fastapi/ops/create.py.j2",
        body_extra={},
        sql_verb="insert",
        needs_utils=False,
    )
