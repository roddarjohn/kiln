"""Permissions operation: ``GET /permissions`` endpoints.

When a resource sets
:attr:`~be.config.schema.ResourceConfig.permissions_endpoint`,
this op emits two route handlers that return only the
:class:`~ingot.actions.ActionRef` list -- no resource payload:

* ``GET /{pk}/permissions`` -> object-scope actions for one row.
* ``GET /permissions``      -> collection-scope actions.

The frontend uses these to refresh button visibility (e.g. after
a state transition) without paying for a full resource fetch.
The two handlers reference the same per-app action registry that
the dump path uses, so the visibility decisions and the
execution-time gates can never drift.

Runs at resource scope without ``after_children`` so the routes
emit *before* Get/Update/Delete in the generated route file --
critical because FastAPI matches in declaration order, and
``GET /{pk}`` would otherwise swallow the literal
``/permissions`` segment for ``str`` primary keys.
"""

from typing import TYPE_CHECKING

from be.config.schema import PYTHON_TYPES
from be.operations._naming import (
    app_module_for,
    collection_specs_const,
    object_specs_const,
)
from be.operations.renderers import FETCH_OR_404_IMPORT
from be.operations.types import RouteHandler, RouteParam, TestCase
from foundry.naming import Name, prefix_import
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("permissions", scope="resource")
class Permissions:
    """Emit ``GET /permissions`` endpoints for a resource.

    Gated by :attr:`~be.config.schema.ResourceConfig.permissions_endpoint`; when
    unset, :meth:`when` returns ``False`` and nothing is emitted.
    """

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run only when the resource opts in.

        Args:
            ctx: Build context with the resource config.

        Returns:
            ``True`` when ``permissions_endpoint`` is set on the
            resource; otherwise ``False`` (engine skips the op).

        """
        return ctx.instance.permissions_endpoint

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Emit object + collection permissions handlers.

        Args:
            ctx: Build context for the resource.
            _options: Unused -- the op takes no per-resource config
                beyond the boolean opt-in.

        Yields:
            Two :class:`~be.operations.types.RouteHandler` outputs
            and matching :class:`~be.operations.types.TestCase`
            entries.  Auth threads ``session`` through on its
            after-children pass.

        """
        resource = ctx.instance
        _, model = Name.from_dotted(resource.model)

        actions_module = prefix_import(
            ctx.package_prefix,
            app_module_for(resource.model),
            "actions",
        )
        object_const = object_specs_const(model)
        collection_const = collection_specs_const(model)

        common_imports: list[tuple[str, str]] = [
            ("ingot.actions", "ActionRef"),
            ("ingot.actions", "available_actions"),
        ]

        # ------------------------------------------------------------
        # Object-scope endpoint: GET /{pk}/permissions
        # ------------------------------------------------------------
        object_handler = RouteHandler(
            method="GET",
            path=f"/{{{resource.pk}}}/permissions",
            function_name=f"permissions_{model.lower}_object",
            op_name="permissions",
            params=[
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                ),
            ],
            response_model="list[ActionRef]",
            response_schema_module="ingot.actions",
            return_type="list[ActionRef]",
            doc=f"List actions available on a single {model.pascal}.",
            body_template="fastapi/ops/permissions_object.py.j2",
            body_context={
                "object_specs_const": object_const,
            },
            extra_imports=[
                *common_imports,
                ("sqlalchemy", "select"),
                FETCH_OR_404_IMPORT,
                (actions_module, object_const),
            ],
        )

        # ------------------------------------------------------------
        # Collection-scope endpoint: GET /permissions
        # ------------------------------------------------------------
        collection_handler = RouteHandler(
            method="GET",
            path="/permissions",
            function_name=f"permissions_{model.lower}_collection",
            op_name="permissions",
            params=[],
            response_model="list[ActionRef]",
            response_schema_module="ingot.actions",
            return_type="list[ActionRef]",
            doc=f"List collection-scoped actions on {model.pascal}.",
            body_template="fastapi/ops/permissions_collection.py.j2",
            body_context={
                "collection_specs_const": collection_const,
            },
            extra_imports=[
                *common_imports,
                (actions_module, collection_const),
            ],
        )

        yield object_handler
        yield collection_handler

        yield TestCase(
            op_name="permissions",
            method="get",
            path=f"/{{{resource.pk}}}/permissions",
            status_success=200,
            status_not_found=404,
            action_name="permissions",
        )
        yield TestCase(
            op_name="permissions",
            method="get",
            path="/permissions",
            status_success=200,
            action_name="permissions_collection",
        )
