"""Update operation: PATCH /{pk} -- partially update a resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import (
    Field,
    RouteHandler,
    RouteParam,
    SchemaClass,
    TestCase,
)
from kiln.generators._helpers import PYTHON_TYPES
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
class UpdateRoute(RouteHandler):
    """Route handler emitted by the :class:`Update` operation."""

    op_name: str = "update"


@operation("update", scope="resource", requires=["create"])
class Update:
    """PATCH /{pk} -- partially update a resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext,
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for PATCH /{pk}.

        Args:
            ctx: Build context with resource config.
            options: Parsed :class:`FieldsOptions`.

        Yields:
            The ``{Model}UpdateRequest`` schema (all fields
            optional), the route handler, and a test case.

        """
        resource = ctx.instance
        _, model = Name.from_dotted(resource.model)
        pk_name = resource.pk
        pk_py_type = PYTHON_TYPES[resource.pk_type]
        request_schema = model.suffixed("UpdateRequest")

        yield SchemaClass(
            name=request_schema,
            fields=[
                Field(name=f.name, py_type=f.py_type, optional=True)
                for f in _field_dicts(options.fields)
            ],
            doc=f"Request body for updating a {model.pascal}.",
        )

        handler = UpdateRoute(
            method="PATCH",
            path=f"/{{{pk_name}}}",
            function_name=f"update_{model.lower}",
            doc=f"Update a {model.pascal} by {pk_name}.",
            request_schema=request_schema,
        )
        handler.params.append(RouteParam(name=pk_name, annotation=pk_py_type))
        yield handler

        yield TestCase(
            op_name="update",
            method="patch",
            path=f"/{{{pk_name}}}",
            status_success=200,
            status_not_found=404,
            status_invalid=422,
            has_request_body=True,
            request_schema=request_schema,
        )


@FASTAPI_REGISTRY.renders(UpdateRoute, tags=FASTAPI_TAGS)
def _render(h: UpdateRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        h,
        ctx,
        body_template="fastapi/ops/update.py.j2",
        body_extra={},
        sql_verb="update",
        needs_utils=True,
    )
