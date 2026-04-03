"""Generator that produces the shared utils module for a FastAPI app."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators._helpers import prefix_path
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig


class UtilsGenerator:
    """Produces ``{prefix}/{module}/utils.py`` with shared route helpers.

    Currently generates ``get_object_from_query_or_404``, a small async
    helper used by GET routes that have explicit field schemas.
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "utils"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when there are any resources to generate routes for.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.resources)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate the shared utils file.

        Args:
            config: The validated kiln configuration.

        Returns:
            A single :class:`~kiln.generators.base.GeneratedFile` at
            ``{prefix}/{module}/utils.py``.

        """
        tmpl = env.get_template("fastapi/utils.py.j2")
        return [
            GeneratedFile(
                path=prefix_path(
                    config.package_prefix, config.module, "utils.py"
                ),
                content=tmpl.render(),
            )
        ]
