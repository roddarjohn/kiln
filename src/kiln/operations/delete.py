"""Delete operation: DELETE /{pk} -- delete a resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry.naming import Name
from foundry.operation import EmptyOptions, operation
from foundry.outputs import RouteHandler, RouteParam, TestCase
from kiln.generators._helpers import PYTHON_TYPES
from kiln.renderers import registry
from kiln.renderers.fastapi import build_handler_fragment, utils_imports

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx
    from kiln.config.schema import ResourceConfig


@dataclass
class DeleteRoute(RouteHandler):
    """Route handler emitted by the :class:`Delete` operation."""


@operation("delete", scope="resource", requires=["update"])
class Delete:
    """DELETE /{pk} -- delete a resource."""

    def build(
        self,
        ctx: BuildContext[ResourceConfig],
        _options: EmptyOptions,
    ) -> Iterable[object]:
        """Produce output for DELETE /{pk}.

        Args:
            ctx: Build context with resource config.
            _options: Unused.

        Yields:
            The route handler and a test case.

        """
        _, model = Name.from_dotted(ctx.instance.model)

        yield DeleteRoute(
            method="DELETE",
            path=f"/{{{ctx.instance.pk}}}",
            function_name=f"delete_{model.lower}",
            params=[
                RouteParam(
                    name=ctx.instance.pk,
                    annotation=PYTHON_TYPES[ctx.instance.pk_type],
                )
            ],
            status_code=204,
            doc=f"Delete a {model.pascal} by {ctx.instance.pk}.",
        )

        yield TestCase(
            op_name="delete",
            method="delete",
            path=f"/{{{ctx.instance.pk}}}",
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
