"""Delete operation: DELETE /{pk} -- delete a resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry.naming import Name
from foundry.operation import EmptyOptions, operation
from foundry.outputs import RouteHandler, RouteParam, TestCase
from kiln.generators._helpers import PYTHON_TYPES
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
class DeleteRoute(RouteHandler):
    """Route handler emitted by the :class:`Delete` operation."""

    op_name: str = "delete"


@operation("delete", scope="resource", requires=["update"])
class Delete:
    """DELETE /{pk} -- delete a resource."""

    def build(
        self,
        ctx: BuildContext,
        _options: EmptyOptions,
    ) -> Iterable[object]:
        """Produce output for DELETE /{pk}.

        Args:
            ctx: Build context with resource config.
            _options: Unused.

        Yields:
            The route handler and a test case.

        """
        resource = ctx.instance
        _, model = Name.from_dotted(resource.model)
        pk_name = resource.pk
        pk_py_type = PYTHON_TYPES[resource.pk_type]

        handler = DeleteRoute(
            method="DELETE",
            path=f"/{{{pk_name}}}",
            function_name=f"delete_{model.lower}",
            status_code=204,
            doc=f"Delete a {model.pascal} by {pk_name}.",
        )
        handler.params.append(RouteParam(name=pk_name, annotation=pk_py_type))
        yield handler

        yield TestCase(
            op_name="delete",
            method="delete",
            path=f"/{{{pk_name}}}",
            status_success=204,
            status_not_found=404,
        )


@FASTAPI_REGISTRY.renders(DeleteRoute, tags=FASTAPI_TAGS)
def _render(h: DeleteRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        h,
        ctx,
        body_template="fastapi/ops/delete.py.j2",
        body_extra={},
        sql_verb="delete",
        needs_utils=True,
    )
