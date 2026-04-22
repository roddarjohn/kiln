"""Action operation: custom endpoint via function introspection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, TestCase
from foundry.render import registry
from kiln.operations._introspect import introspect_action_fn
from kiln.operations._render import build_handler_fragment

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from foundry.render import Fragment, RenderCtx
    from kiln.config.schema import ResourceConfig


@dataclass
class ActionRoute(RouteHandler):
    """Route handler emitted by the :class:`Action` operation."""


@operation("action", scope="resource")
class Action:
    """Custom action endpoint via function introspection."""

    class Options(BaseModel):
        """Options for action operations.

        ``name`` is the action's user-facing name (e.g.
        ``"publish"``).  The engine injects it from the matching
        :class:`~kiln.config.schema.OperationConfig` entry so
        ``build`` doesn't have to reach for it via the scope's
        ``instance_id``.
        """

        name: str
        fn: str

    def build(
        self,
        ctx: BuildContext[ResourceConfig],
        options: Options,
    ) -> Iterable[object]:
        """Produce output for a custom action endpoint.

        Args:
            ctx: Build context with resource config.
            options: Parsed Action.Options with ``name`` and
                ``fn`` path.

        Yields:
            The route handler and a test case.

        """
        action_name = Name(options.name)
        info = introspect_action_fn(options.fn, ctx.instance.model)

        if info.is_object_action:
            path = f"/{{{ctx.instance.pk}}}/{action_name.slug}"

        else:
            path = f"/{action_name.slug}"

        yield ActionRoute(
            method="POST",
            path=path,
            function_name=f"{action_name.raw}_action",
            response_model=info.response_class,
            return_type=info.response_class,
            doc=f"Execute {action_name.raw} action.",
            request_schema=info.request_class,
        )

        yield TestCase(
            op_name="action",
            method="post",
            path=path,
            status_success=200,
            status_not_found=(404 if info.is_object_action else None),
            has_request_body=bool(info.request_class),
            request_schema=info.request_class,
            action_name=action_name.raw,
        )


@registry.renders(ActionRoute)
def _render(handler: ActionRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        handler,
        ctx,
        body_template="fastapi/ops/action.py.j2",
        body_extra={
            "function_name": handler.function_name,
            "method": handler.method.lower(),
            "path": handler.path,
            "response_class": handler.response_model,
            "request_class": handler.request_schema,
        },
    )
