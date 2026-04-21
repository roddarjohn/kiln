"""Router operations: resource-level and project-level routers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import RouterMount, StaticFile

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.engine import BuildContext


@operation("router", scope="project")
class Router:
    """Generate ``{module}/routes/__init__.py``.

    Mounts every resource's router into a single router
    object.  Runs at project scope and reads the config's
    resources list.
    """

    def build(
        self,
        ctx: BuildContext,
        _options: BaseModel,
    ) -> list[RouterMount | StaticFile]:
        """Produce router mounts for each resource.

        Args:
            ctx: Build context; instance is the config.
            _options: Unused.

        Returns:
            List of :class:`RouterMount` and :class:`StaticFile`
            objects.

        """
        config = ctx.config
        module = getattr(config, "module", "app")
        resources = getattr(config, "resources", [])

        mounts: list[RouterMount | StaticFile] = []
        for resource in resources:
            model = getattr(resource, "model", "")
            _, _, class_name = model.rpartition(".")
            lower = class_name.lower()
            mounts.append(
                RouterMount(
                    module=f"{module}.routes.{lower}",
                    alias=f"{lower}_router",
                )
            )

        if not mounts:
            return []

        mounts.append(
            StaticFile(
                path=f"{module}/routes/__init__.py",
                template="fastapi/router.py.j2",
                context={
                    "module": module,
                    "routes": [
                        {
                            "module_name": m.alias.removesuffix(
                                "_router",
                            ),
                            "alias": m.alias,
                        }
                        for m in mounts
                        if isinstance(m, RouterMount)
                    ],
                },
            )
        )
        return mounts


@operation("project_router", scope="project")
class ProjectRouter:
    """Generate ``routes/__init__.py`` mounting all apps."""

    def build(
        self,
        ctx: BuildContext,
        _options: BaseModel,
    ) -> list[StaticFile]:
        """Produce the project-level router file.

        Only produces output for multi-app configs (those
        with an ``apps`` list).

        Args:
            ctx: Build context; instance is the project config.
            _options: Unused.

        Returns:
            Single :class:`StaticFile` for the project router,
            or empty list for single-level configs.

        """
        config = ctx.config
        apps = getattr(config, "apps", [])
        if not apps:
            return []

        pkg = getattr(config, "package_prefix", "")
        has_auth = getattr(config, "auth", None) is not None
        auth_module = f"{pkg}.auth" if pkg else "auth"

        return [
            StaticFile(
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
                        for app_ref in apps
                    ],
                },
            )
        ]
