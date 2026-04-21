"""Action operation: custom endpoint via function introspection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, TestCase
from kiln.operations._introspect import introspect_action_fn
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
class ActionRoute(RouteHandler):
    """Route handler emitted by the :class:`Action` operation."""

    op_name: str = "action"


@operation("action", scope="resource")
class Action:
    """Custom action endpoint via function introspection."""

    class Options(BaseModel):
        """Options for action operations."""

        fn: str

    def build(
        self,
        ctx: BuildContext,
        options: Options,
    ) -> Iterable[object]:
        """Produce output for a custom action endpoint.

        Args:
            ctx: Build context with resource config.
            options: Parsed Action.Options with ``fn`` path.

        Yields:
            The route handler and a test case.

        """
        resource = ctx.instance
        action_name = Name(ctx.instance_id)
        info = introspect_action_fn(options.fn, resource.model)

        pk_name = resource.pk
        if info.is_object_action:
            path = f"/{{{pk_name}}}/{action_name.slug}"
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


@FASTAPI_REGISTRY.renders(ActionRoute, tags=FASTAPI_TAGS)
def _render(h: ActionRoute, ctx: RenderCtx) -> Fragment:
    return build_handler_fragment(
        h,
        ctx,
        body_template="fastapi/ops/action.py.j2",
        body_extra={
            "function_name": h.function_name,
            "method": h.method.lower(),
            "path": h.path,
            "response_class": h.response_model,
            "request_class": h.request_schema,
        },
        sql_verb=None,
        needs_utils=False,
    )
