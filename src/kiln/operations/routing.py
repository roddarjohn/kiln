"""Router operations: per-app router and project router."""

from typing import TYPE_CHECKING, cast

from foundry.operation import operation
from foundry.outputs import StaticFile
from kiln.operations.types import RouteHandler

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from kiln.config.schema import App, ProjectConfig, ResourceConfig


@operation("router", scope="app", after_children=True)
class Router:
    """Generate one app's ``{module}/routes/__init__.py``.

    Runs in the post-children phase of the app scope so the build
    store is fully populated with resource-scope output beneath
    this app.  Emits one
    :class:`~kiln.operations.types.RouterMount` per resource that
    produced at least one
    :class:`~kiln.operations.types.RouteHandler` plus a single
    :class:`~foundry.outputs.StaticFile` that aggregates them into
    the app's router module.
    """

    def build(
        self,
        ctx: BuildContext[App, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce this app's router-aggregation file.

        Args:
            ctx: Build context for one
                :class:`~kiln.config.schema.App`; ``store`` is
                fully populated with resource-scope output because
                ``after_children=True``.
            _options: Unused.

        Yields:
            A single :class:`~foundry.outputs.StaticFile` for the
            app's routes package, carrying one ``routes`` entry
            per resource that produced a
            :class:`~kiln.operations.types.RouteHandler`.  Nothing
            is yielded when no resource in the app produced a
            handler.

        """
        app = ctx.instance
        app_config = app.config
        module = app_config.module

        # Route handlers live at operation scope (grandchildren of
        # the app), so walk each resource and ask whether any of
        # its descendants produced a handler.
        mounted: list[ResourceConfig] = []

        for resource_id, resource_obj in ctx.store.children(
            ctx.instance_id,
            child_scope="resource",
        ):
            if ctx.store.outputs_under(resource_id, RouteHandler):
                mounted.append(cast("ResourceConfig", resource_obj))

        if not mounted:
            return

        slugs = [_resource_module_slug(resource) for resource in mounted]
        yield StaticFile(
            path=f"{module}/routes/__init__.py",
            template="fastapi/router.py.j2",
            context={
                "module": module,
                "routes": [
                    {
                        "module_name": slug,
                        "alias": f"{slug}_router",
                    }
                    for slug in slugs
                ],
            },
        )


@operation("project_router", scope="project")
class ProjectRouter:
    """Generate ``routes/__init__.py`` mounting all apps."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the project-level router file.

        Only produces output for configs that have an ``apps``
        list.  :class:`~kiln.config.schema.ProjectConfig` wraps a
        single-app shorthand into an implicit app with
        ``prefix=""`` at validation time, so every project config
        routed through ``foundry generate`` has at least one app and
        this op runs unconditionally in the normal pipeline.

        Args:
            ctx: Build context; instance is the project config.
            _options: Unused.

        Yields:
            Single :class:`~foundry.outputs.StaticFile` for the project router,
            or nothing for configs that have no apps at all.

        """
        config = ctx.instance

        if not config.apps:
            return

        package_prefix = config.package_prefix
        has_auth = config.auth is not None
        auth_module = f"{package_prefix}.auth" if package_prefix else "auth"
        has_telemetry = config.telemetry is not None
        telemetry_module = (
            f"{package_prefix}.telemetry" if package_prefix else "telemetry"
        )

        yield StaticFile(
            path="routes/__init__.py",
            template="fastapi/project_router.py.j2",
            context={
                "has_auth": has_auth,
                "auth_module": auth_module,
                "has_telemetry": has_telemetry,
                "telemetry_module": telemetry_module,
                "apps": [
                    {
                        "module": (
                            f"{package_prefix}.{app.config.module}"
                            if package_prefix
                            else app.config.module
                        ),
                        "alias": app.config.module,
                        "prefix": app.prefix,
                    }
                    for app in config.apps
                ],
            },
        )


def _resource_module_slug(resource: ResourceConfig) -> str:
    """Return the Python-module slug derived from *resource*'s model.

    The slug names the generated ``{app}/routes/{slug}.py`` file
    and its router alias ``{slug}_router``.  It is derived from
    the class name of ``resource.model`` (lowercased) rather than
    the store's instance id — the latter is opaque and must not
    leak into generated code.

    Args:
        resource: Resource config entry.

    Returns:
        Lowercase class name, e.g. ``"article"`` for
        ``"blog.models.Article"``.

    """
    _, _, class_name = resource.model.rpartition(".")
    return class_name.lower()
