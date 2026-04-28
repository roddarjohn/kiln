"""Delete operation: DELETE /{pk} -- delete a resource."""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations.renderers import gate_wiring, utils_imports
from be.operations.types import RouteHandler, RouteParam, TestCase
from foundry.naming import Name
from foundry.operation import EmptyOptions, operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import (
        OperationConfig,
        ProjectConfig,
        ResourceConfig,
    )
    from foundry.engine import BuildContext


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
        ctx: BuildContext[OperationConfig, ProjectConfig],
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

        gate_ctx, gate_imports = gate_wiring(
            ctx.instance,
            resource,
            ctx.package_prefix,
            is_object_scope=True,
        )
        # Gated delete fetches the row first so the guard can
        # inspect state; ungated delete keeps the single-statement
        # DELETE form.
        gate_extra_imports = [("sqlalchemy", "select")] if gate_ctx else []

        yield RouteHandler(
            method="DELETE",
            path=f"/{{{resource.pk}}}",
            function_name=f"delete_{model.lower}",
            op_name=ctx.instance.name,
            params=[
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                )
            ],
            status_code=204,
            doc=f"Delete a {model.pascal} by {resource.pk}.",
            body_template="fastapi/ops/delete.py.j2",
            body_context=gate_ctx,
            extra_imports=[
                ("sqlalchemy", "delete"),
                *utils_imports(),
                *gate_extra_imports,
                *gate_imports,
            ],
        )

        yield TestCase(
            op_name="delete",
            method="delete",
            path=f"/{{{resource.pk}}}",
            status_success=204,
            status_not_found=404,
        )
