"""Action operation: custom endpoint via function introspection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, TestCase
from kiln.operations._introspect import introspect_action_fn

if TYPE_CHECKING:
    from foundry.engine import BuildContext


@operation("action", scope="resource")
class Action:
    """Custom action endpoint via function introspection."""

    class Options(BaseModel):
        """Options for action operations."""

        fn: str

    def build(
        self,
        ctx: BuildContext,
        options: BaseModel,
    ) -> list[object]:
        """Produce output for a custom action endpoint.

        Args:
            ctx: Build context with resource config.
            options: Parsed Action.Options with ``fn`` path.

        Returns:
            List of objects (handler + test case).

        """
        resource = ctx.instance
        fn_dotted = getattr(options, "fn", "")
        action_name = Name(ctx.instance_id)

        info = introspect_action_fn(fn_dotted, resource.model)

        # Determine path
        pk_name = resource.pk
        if info.is_object_action:
            path = f"/{{{pk_name}}}/{action_name.slug}"
        else:
            path = f"/{action_name.slug}"

        handler = RouteHandler(
            method="POST",
            path=path,
            function_name=f"{action_name.raw}_action",
            op_name="action",
            response_model=info.response_class,
            return_type=info.response_class,
            doc=f"Execute {action_name.raw} action.",
            request_schema=info.request_class,
        )

        test = TestCase(
            op_name="action",
            method="post",
            path=path,
            status_success=200,
            status_not_found=(404 if info.is_object_action else None),
            has_request_body=bool(info.request_class),
            request_schema=info.request_class,
            action_name=action_name.raw,
        )

        return [handler, test]
