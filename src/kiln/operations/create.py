"""Create operation: POST / -- create a new resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, SchemaClass, TestCase
from foundry.render import registry
from kiln.operations._render import build_handler_fragment
from kiln.operations._shared import FieldsOptions, _field_dicts

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx
    from kiln.config.schema import OperationConfig, ResourceConfig


@dataclass
class CreateRoute(RouteHandler):
    """Route handler emitted by the :class:`Create` operation."""


@operation(
    "create",
    scope="operation",
    dispatch_on="name",
    requires=["list"],
)
class Create:
    """POST / -- create a new resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for POST /.

        Args:
            ctx: Build context for the ``"create"`` operation entry.
            options: Parsed :class:`FieldsOptions`.

        Yields:
            The ``{Model}CreateRequest`` schema, the route handler,
            and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
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


@registry.renders(CreateRoute)
def _render(handler: CreateRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        handler,
        ctx,
        body_template="fastapi/ops/create.py.j2",
        body_extra={},
        extra_imports=[("sqlalchemy", "insert")],
    )
