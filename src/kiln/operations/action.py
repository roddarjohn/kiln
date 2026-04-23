"""Action operation: custom endpoint via function introspection."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from kiln.config.schema import PYTHON_TYPES
from kiln.operations._introspect import introspect_action_fn
from kiln.operations.renderers import utils_imports
from kiln.operations.types import RouteHandler, RouteParam, TestCase

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import OperationConfig, ResourceConfig


@operation("action", scope="operation", dispatch_on="type")
class Action:
    """Custom action endpoint via function introspection.

    Dispatches on :attr:`~kiln.config.schema.OperationConfig.type`
    ``== "action"``.  Every action config declares ``type: "action"``
    explicitly (typically via ``kiln/resources/presets.libsonnet``),
    with a user-defined ``name`` and the ``fn`` import path to
    introspect.
    """

    class Options(BaseModel):
        """Options for action operations."""

        fn: str

    def build(
        self,
        ctx: BuildContext[OperationConfig],
        options: Options,
    ) -> Iterable[object]:
        """Produce output for a custom action endpoint.

        Args:
            ctx: Build context for the action's operation entry.
            options: Parsed ``Options`` with the ``fn`` dotted path.

        Yields:
            The route handler and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        action_name = Name(ctx.instance.name)
        info = introspect_action_fn(options.fn, resource.model)
        fn_module, fn_name = options.fn.rsplit(".", 1)

        if info.is_object_action:
            path = f"/{{{resource.pk}}}/{action_name.slug}"
            params = [
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                ),
            ]
            extra_imports = [
                (fn_module, fn_name),
                ("sqlalchemy", "select"),
                *utils_imports(),
            ]

        else:
            path = f"/{action_name.slug}"
            params = []
            extra_imports = [(fn_module, fn_name)]

        if info.request_class is not None:
            params.append(
                RouteParam(name="body", annotation=info.request_class),
            )

        yield RouteHandler(
            method="POST",
            path=path,
            function_name=f"{action_name.raw}_action",
            params=params,
            response_model=info.response_class,
            return_type=info.response_class,
            doc=f"Execute {action_name.raw} action.",
            request_schema=info.request_class,
            body_template="fastapi/ops/action.py.j2",
            body_context={
                "is_object_action": info.is_object_action,
                "fn_name": fn_name,
                "model_param_name": info.model_param_name,
                "has_request_body": info.request_class is not None,
            },
            extra_imports=extra_imports,
        )

        yield TestCase(
            op_name="action",
            method="post",
            path=path,
            status_success=200,
            status_not_found=404 if info.is_object_action else None,
            has_request_body=info.request_class is not None,
            request_schema=info.request_class,
            action_name=action_name.raw,
        )
