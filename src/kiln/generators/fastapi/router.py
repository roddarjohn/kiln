"""Generator that produces the aggregating FastAPI router file."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators._helpers import prefix_path, split_dotted_class
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig


class RouterGenerator:
    """Produces ``{module}/routes/__init__.py`` mounting every resource router.

    The file imports a router from each generated resource route file and
    combines them into a single ``router`` object that the application's
    ``main.py`` can include::

        from app.routes import router
        app.include_router(router)
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "router"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when there are any resources to aggregate.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.resources)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate the aggregating router file.

        Args:
            config: The validated kiln configuration.

        Returns:
            A single :class:`~kiln.generators.base.GeneratedFile`
            at ``{module}/routes/__init__.py``.

        """
        return [
            GeneratedFile(
                path=prefix_path(
                    config.package_prefix,
                    config.module,
                    "routes",
                    "__init__.py",
                ),
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
    routes = []
    for resource in config.resources:
        _, class_name = split_dotted_class(resource.model)
        module_name = class_name.lower()
        routes.append(
            {"module_name": module_name, "alias": f"{module_name}_router"}
        )

    tmpl = env.get_template("fastapi/router.py.j2")
    return tmpl.render(
        module=config.module,
        routes=routes,
    )
