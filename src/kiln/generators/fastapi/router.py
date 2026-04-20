"""App-level router that mounts all resource routers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln_core import GeneratedFile, Name

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig


def generate_app_router(
    config: KilnConfig,
) -> list[GeneratedFile]:
    """Generate ``{module}/routes/__init__.py``.

    Mounts every resource's router into a single ``router``
    object that the application includes.

    Args:
        config: The validated app-level kiln configuration.

    Returns:
        A single :class:`GeneratedFile`.

    """
    routes = []
    for resource in config.resources:
        _, model = Name.from_dotted(resource.model)
        module_name = model.lower
        routes.append(
            {
                "module_name": module_name,
                "alias": f"{module_name}_router",
            }
        )

    tmpl = env.get_template("fastapi/router.py.j2")
    content = tmpl.render(module=config.module, routes=routes)
    return [
        GeneratedFile(
            path=f"{config.module}/routes/__init__.py",
            content=content,
        )
    ]
