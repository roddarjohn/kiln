"""Generator that produces the aggregating FastAPI router file."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig


class RouterGenerator:
    """Produces ``api/__init__.py`` that mounts every generated router.

    The file imports a router from each CRUD route file and each view
    route file, then combines them into a single ``router`` object
    that the application's ``main.py`` can include::

        from app.api import router
        app.include_router(router)
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "router"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when there are any routes to aggregate.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.routes)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate the aggregating router file.

        Args:
            config: The validated kiln configuration.

        Returns:
            A single :class:`~kiln.generators.base.GeneratedFile`
            at ``api/__init__.py``.

        """
        return [
            GeneratedFile(
                path=f"{config.module}/routes/__init__.py",
                content=_render_router(config),
            )
        ]


def _render_router(config: KilnConfig) -> str:
    """Render the aggregating router source.

    Args:
        config: The validated kiln configuration.

    Returns:
        Python source string.

    """
    from kiln.config.schema import (  # noqa: PLC0415
        ActionRouteConfig,
        CRUDRouteConfig,
        ViewRouteConfig,
    )

    route_names: list[str] = []
    for r in config.routes:
        if isinstance(r, CRUDRouteConfig):
            route_names.append(r.model.lower())
        elif isinstance(r, ViewRouteConfig):
            route_names.append(r.view)
        elif isinstance(r, ActionRouteConfig):
            route_names.append(r.name)

    routes = [
        {"module_name": name, "alias": f"{name}_router"} for name in route_names
    ]

    tmpl = env.get_template("fastapi/router.py.j2")
    return tmpl.render(
        module=config.module,
        routes=routes,
    )
