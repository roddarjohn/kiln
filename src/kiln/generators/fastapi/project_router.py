"""Generator that produces the root router for project-level configs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig


class ProjectRouterGenerator:
    """Produces a root ``routes/__init__.py`` that mounts every app router.

    Only runs when the config has an ``apps`` list (project mode).
    The result can be included directly in a FastAPI application::

        from .routes import router
        app.include_router(router)
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "project_router"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when there are apps to mount.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.apps)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate the root router file.

        Args:
            config: The validated project-level kiln configuration.

        Returns:
            A single :class:`~kiln.generators.base.GeneratedFile`
            at ``routes/__init__.py``.

        """
        tmpl = env.get_template("fastapi/project_router.py.j2")
        content = tmpl.render(
            apps=[
                {"module": app_ref.config.module, "prefix": app_ref.prefix}
                for app_ref in config.apps
            ]
        )
        return [GeneratedFile(path="routes/__init__.py", content=content)]
