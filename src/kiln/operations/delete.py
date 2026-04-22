"""Delete operation: DELETE /{pk} -- delete a resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from foundry.naming import Name
from foundry.operation import EmptyOptions, operation
from foundry.outputs import RouteHandler, RouteParam, TestCase
from foundry.render import registry
from kiln._helpers import PYTHON_TYPES
from kiln.operations._render import build_handler_fragment, utils_imports

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx
    from kiln.config.schema import OperationConfig, ResourceConfig


@dataclass
class DeleteRoute(RouteHandler):
    """Route handler emitted by the :class:`Delete` operation."""


@operation(
    "delete",
    scope="operation",
    dispatch_on="name",
    requires=["update"],
)
class Delete:
    """DELETE /{pk} -- delete a resource."""

    def build(
        self,
        ctx: BuildContext[OperationConfig],
        _options: EmptyOptions,
    ) -> Iterable[object]:
        """Produce output for DELETE /{pk}.

        Args:
            ctx: Build context for the ``"delete"`` operation entry.
            _options: Unused.

        Yields:
            The route handler and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)

        yield DeleteRoute(
            method="DELETE",
            path=f"/{{{resource.pk}}}",
            function_name=f"delete_{model.lower}",
            params=[
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                )
            ],
            status_code=204,
            doc=f"Delete a {model.pascal} by {resource.pk}.",
        )

        yield TestCase(
            op_name="delete",
            method="delete",
            path=f"/{{{resource.pk}}}",
            status_success=204,
            status_not_found=404,
        )


@registry.renders(DeleteRoute)
def _render(handler: DeleteRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        handler,
        ctx,
        body_template="fastapi/ops/delete.py.j2",
        body_extra={},
        extra_imports=[("sqlalchemy", "delete"), *utils_imports()],
    )
