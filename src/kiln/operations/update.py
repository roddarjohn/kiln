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
from kiln.renderers import registry
from kiln.renderers.fastapi import build_handler_fragment, utils_imports

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx
    from kiln.config.schema import ResourceConfig


@dataclass
class UpdateRoute(RouteHandler):
    """Route handler emitted by the :class:`Update` operation."""


@operation("update", scope="resource", requires=["create"])
class Update:
    """PATCH /{pk} -- partially update a resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[ResourceConfig],
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
        _, model = Name.from_dotted(ctx.instance.model)
        request_schema = model.suffixed("UpdateRequest")

        yield SchemaClass(
            name=request_schema,
            fields=[
                Field(name=f.name, py_type=f.py_type, optional=True)
                for f in _field_dicts(options.fields)
            ],
            doc=f"Request body for updating a {model.pascal}.",
        )

        yield UpdateRoute(
            method="PATCH",
            path=f"/{{{ctx.instance.pk}}}",
            function_name=f"update_{model.lower}",
            params=[
                RouteParam(
                    name=ctx.instance.pk,
                    annotation=PYTHON_TYPES[ctx.instance.pk_type],
                )
            ],
            doc=f"Update a {model.pascal} by {ctx.instance.pk}.",
            request_schema=request_schema,
        )

        yield TestCase(
            op_name="update",
            method="patch",
            path=f"/{{{ctx.instance.pk}}}",
            status_success=200,
            status_not_found=404,
            status_invalid=422,
            has_request_body=True,
            request_schema=request_schema,
        )


@registry.renders(UpdateRoute)
def _render(handler: UpdateRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        handler,
        ctx,
        body_template="fastapi/ops/update.py.j2",
        body_extra={},
        extra_imports=[("sqlalchemy", "update"), *utils_imports()],
    )
