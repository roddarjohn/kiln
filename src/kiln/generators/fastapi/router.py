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
        has_crud = any(m.crud is not None for m in config.models)
        return has_crud or bool(config.views)

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
    crud_models = [m for m in config.models if m.crud is not None]
    tmpl = env.get_template("fastapi/router.py.j2")
    return tmpl.render(
        module=config.module,
        crud_models=[
            {"lower": m.name.lower(), "alias": f"{m.name.lower()}_router"}
            for m in crud_models
        ],
        views=[
            {"name": v.name, "alias": f"{v.name}_router"} for v in config.views
        ],
    )
