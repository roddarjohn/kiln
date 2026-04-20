"""Root router that mounts all app routers in a multi-app project."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln_core import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig


def generate_project_router(
    config: KilnConfig,
) -> list[GeneratedFile]:
    """Generate ``routes/__init__.py`` mounting every app.

    Args:
        config: The validated kiln configuration (must have
            ``apps``).

    Returns:
        A single :class:`GeneratedFile`.

    """
    tmpl = env.get_template("fastapi/project_router.py.j2")
    pkg = config.package_prefix
    has_auth = config.auth is not None
    auth_module = f"{pkg}.auth" if pkg else "auth"
    content = tmpl.render(
        has_auth=has_auth,
        auth_module=auth_module,
        apps=[
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
    )
    return [GeneratedFile(path="routes/__init__.py", content=content)]
