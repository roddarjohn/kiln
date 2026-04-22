"""Action operation: custom endpoint via function introspection."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import RouteHandler, TestCase
from kiln.operations._introspect import introspect_action_fn

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import OperationConfig, ResourceConfig


@operation("action", scope="operation")
class Action:
    """Custom action endpoint via function introspection.

    Dispatches on the presence of a ``fn`` attribute rather than
    a literal name match: any :class:`OperationConfig` whose
    ``options`` include ``fn`` becomes an action.
    """

    class Options(BaseModel):
        """Options for action operations."""

        fn: str

    def when(self, ctx: BuildContext[OperationConfig]) -> bool:
        """Activate whenever the op config carries an ``fn`` field."""
        return getattr(ctx.instance, "fn", None) is not None

    def build(
        self,
        ctx: BuildContext[OperationConfig],
        options: Options,
    ) -> Iterable[object]:
        """Produce output for a custom action endpoint.

        Args:
            ctx: Build context for the action's operation entry.
            options: Parsed Action.Options with ``fn`` path.

        Yields:
            The route handler and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        action_name = Name(ctx.instance.name)
        info = introspect_action_fn(options.fn, resource.model)

        if info.is_object_action:
            path = f"/{{{resource.pk}}}/{action_name.slug}"

        else:
            path = f"/{action_name.slug}"

        function_name = f"{action_name.raw}_action"
        yield RouteHandler(
            method="POST",
            path=path,
            function_name=function_name,
            response_model=info.response_class,
            return_type=info.response_class,
            doc=f"Execute {action_name.raw} action.",
            request_schema=info.request_class,
            body_template="fastapi/ops/action.py.j2",
            body_context={
                "function_name": function_name,
                "method": "post",
                "path": path,
                "response_class": info.response_class,
                "request_class": info.request_class,
            },
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
