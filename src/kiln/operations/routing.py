"""Router operations: per-app router and project router."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import RouteHandler, RouterMount, StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from foundry.render import BuildStore
    from kiln.config.schema import AppRef, KilnConfig, ResourceConfig


@operation("router", scope="app", after_children=True)
class Router:
    """Generate one app's ``{module}/routes/__init__.py``.

    Runs in the post-children phase of the app scope so the build
    store is fully populated with resource-scope output beneath
    this app.  Emits one :class:`RouterMount` per resource that
    produced at least one :class:`RouteHandler` plus a single
    :class:`StaticFile` that aggregates them into the app's
    router module.
    """

    def build(
        self,
        ctx: BuildContext[AppRef],
        _options: BaseModel,
    ) -> Iterable[RouterMount | StaticFile]:
        """Produce this app's router mount and aggregation file.

        Args:
            ctx: Build context for one :class:`AppRef`; ``store``
                is fully populated with resource-scope output
                because ``after_children=True``.
            _options: Unused.

        Yields:
            One :class:`RouterMount` per mounted resource plus a
            single :class:`StaticFile` for the app's routes
            package.  Nothing is yielded when no resource in the
            app produced a :class:`RouteHandler`.

        """
        app_ref = ctx.instance
        app_config = app_ref.config
        module = app_config.module

        resource_ids = _resource_instance_ids_with_handlers(
            ctx.store,
            app_config.resources,
            ctx.instance_id,
        )
        if not resource_ids:
            return

        for resource_id in resource_ids:
            yield RouterMount(
                module=f"{module}.routes.{resource_id}",
                alias=f"{resource_id}_router",
            )

        yield StaticFile(
            path=f"{module}/routes/__init__.py",
            template="fastapi/router.py.j2",
            context={
                "module": module,
                "routes": [
                    {
                        "module_name": resource_id,
                        "alias": f"{resource_id}_router",
                    }
                    for resource_id in resource_ids
                ],
            },
        )


@operation("project_router", scope="project")
class ProjectRouter:
    """Generate ``routes/__init__.py`` mounting all apps."""

    def build(
        self,
        ctx: BuildContext[KilnConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the project-level router file.

        Only produces output for configs that have an ``apps``
        list.  After :func:`kiln.config.schema.normalize_config`,
        every project config routed through :func:`generate` has
        at least one app (bare top-level resources are wrapped in
        an implicit single app with ``prefix=""``), so this op
        runs unconditionally in the normal pipeline.

        Args:
            ctx: Build context; instance is the project config.
            _options: Unused.

        Yields:
            Single :class:`StaticFile` for the project router,
            or nothing for configs that have no apps at all.

        """
        config = ctx.instance
        if not config.apps:
            return

        package_prefix = config.package_prefix
        has_auth = config.auth is not None
        auth_module = f"{package_prefix}.auth" if package_prefix else "auth"

        yield StaticFile(
            path="routes/__init__.py",
            template="fastapi/project_router.py.j2",
            context={
                "has_auth": has_auth,
                "auth_module": auth_module,
                "apps": [
                    {
                        "module": (
                            f"{package_prefix}.{app_ref.config.module}"
                            if package_prefix
                            else app_ref.config.module
                        ),
                        "alias": app_ref.config.module,
                        "prefix": app_ref.prefix,
                    }
                    for app_ref in config.apps
                ],
            },
        )


def _resource_instance_ids_with_handlers(
    store: BuildStore,
    resources: list[ResourceConfig],
    app_instance_id: str,
) -> list[str]:
    """Return the base instance ids of resources that produced routes.

    Store keys are compounded with the enclosing app's instance
    id (see :meth:`foundry.engine.Engine._visit`), so this
    function looks up
    ``("resource", f"{app_instance_id}/{base}")`` per resource
    but returns the bare base id for output (module paths and
    router aliases are per-app and don't need the prefix).

    Args:
        store: The populated build store.
        resources: Configs for this app's resources.
        app_instance_id: The enclosing app's instance id, used
            as the store-key prefix.

    Returns:
        Base instance IDs, in config order, whose resource scope
        emitted at least one :class:`RouteHandler`.

    """
    instance_ids: list[str] = []
    for resource in resources:
        base_id = _resource_instance_id(resource)
        items = store.get_by_scope(
            "resource",
            f"{app_instance_id}/{base_id}",
        )
        if any(isinstance(item, RouteHandler) for item in items):
            instance_ids.append(base_id)
    return instance_ids


def _resource_instance_id(resource: ResourceConfig) -> str:
    """Compute the engine-generated instance ID for *resource*.

    Matches :func:`foundry.engine._instance_id` for a
    :class:`ResourceConfig`: class name from ``model``, lowercased.

    Args:
        resource: Resource config entry.

    Returns:
        Instance ID string.

    """
    _, _, class_name = resource.model.rpartition(".")
    return class_name.lower()
