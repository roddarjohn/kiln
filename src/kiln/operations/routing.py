"""Router operations: resource-level and project-level routers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import RouteHandler, RouterMount, StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from foundry.render import BuildStore
    from kiln.config.schema import KilnConfig, ResourceConfig


@operation("router", scope="project", after_children=True)
class Router:
    """Generate each app's ``{module}/routes/__init__.py``.

    Runs in the post-children phase of the project scope so the
    build store is fully populated with resource-scope output.
    For every app (or for a single-app config, the project
    itself), emits one router module that mounts every resource
    that produced at least one :class:`RouteHandler`.
    """

    def build(
        self,
        ctx: BuildContext[KilnConfig],
        _options: BaseModel,
    ) -> Iterable[RouterMount | StaticFile]:
        """Produce per-app router mounts and aggregation files.

        Args:
            ctx: Build context; ``store`` is fully populated
                with resource-scope output.
            _options: Unused.

        Yields:
            One :class:`RouterMount` plus a :class:`StaticFile`
            per app with at least one resource that produced
            routes.

        """
        config = ctx.instance
        for module, resources in _apps_to_render(config):
            iids = _resource_iids_with_handlers(ctx.store, resources)
            if not iids:
                continue

            for iid in iids:
                yield RouterMount(
                    module=f"{module}.routes.{iid}",
                    alias=f"{iid}_router",
                )

            yield StaticFile(
                path=f"{module}/routes/__init__.py",
                template="fastapi/router.py.j2",
                context={
                    "module": module,
                    "routes": [
                        {
                            "module_name": iid,
                            "alias": f"{iid}_router",
                        }
                        for iid in iids
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

        Only produces output for multi-app configs (those
        with an ``apps`` list).

        Args:
            ctx: Build context; instance is the project config.
            _options: Unused.

        Yields:
            Single :class:`StaticFile` for the project router,
            or nothing for single-level configs.

        """
        config = ctx.instance
        if not config.apps:
            return

        pkg = config.package_prefix
        has_auth = config.auth is not None
        auth_module = f"{pkg}.auth" if pkg else "auth"

        yield StaticFile(
            path="routes/__init__.py",
            template="fastapi/project_router.py.j2",
            context={
                "has_auth": has_auth,
                "auth_module": auth_module,
                "apps": [
                    {
                        "module": (
                            f"{pkg}.{app_ref.config.module}"
                            if pkg
                            else app_ref.config.module
                        ),
                        "alias": app_ref.config.module,
                        "prefix": app_ref.prefix,
                    }
                    for app_ref in config.apps
                ],
            },
        )


def _apps_to_render(
    config: KilnConfig,
) -> list[tuple[str, list[ResourceConfig]]]:
    """Return ``(module, resources)`` for each app in *config*.

    Multi-app configs expose one entry per :class:`AppRef`; a
    single-app config (resources directly at the root) produces
    one entry with the project's own module and resources.

    Args:
        config: The top-level project config.

    Returns:
        One ``(module, resources)`` tuple per app to render.

    """
    if config.apps:
        return [
            (app_ref.config.module, app_ref.config.resources)
            for app_ref in config.apps
        ]
    if config.resources:
        return [(config.module, config.resources)]
    return []


def _resource_iids_with_handlers(
    store: BuildStore,
    resources: list[ResourceConfig],
) -> list[str]:
    """Return the instance IDs of resources that produced routes.

    The ID convention mirrors :func:`foundry.engine._instance_id`:
    for a :class:`ResourceConfig`, the class name extracted from
    its dotted ``model`` path, lowercased.

    Args:
        store: The populated build store.
        resources: Configs for one app's resources.

    Returns:
        Instance IDs, in config order, whose resource scope
        emitted at least one :class:`RouteHandler`.

    """
    iids: list[str] = []
    for res in resources:
        iid = _resource_iid(res)
        items = store.get_by_scope("resource", iid)
        if any(isinstance(obj, RouteHandler) for obj in items):
            iids.append(iid)
    return iids


def _resource_iid(res: ResourceConfig) -> str:
    """Compute the engine-generated instance ID for *res*.

    Matches :func:`foundry.engine._instance_id` for a
    :class:`ResourceConfig`: class name from ``model``, lowercased.

    Args:
        res: Resource config entry.

    Returns:
        Instance ID string.

    """
    _, _, class_name = res.model.rpartition(".")
    return class_name.lower()
