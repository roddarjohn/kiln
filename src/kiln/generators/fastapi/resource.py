"""Generator that produces FastAPI files for resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators.fastapi.pipeline import generate_resource

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig
    from kiln_core import GeneratedFile


class ResourceGenerator:
    """Produces schema, serializer, and route files per resource.

    For each resource up to four files are emitted:

    * ``{module}/schemas/{model}.py`` -- Pydantic schemas.
    * ``{module}/serializers/{model}.py`` -- serializer function
      (only when a resource schema is generated).
    * ``{module}/routes/{model}.py`` -- async route handlers.
    * ``tests/test_{module}_{model}.py`` -- route tests
      (only when ``generate_tests`` is enabled).
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "resources"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when the config has resources.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.resources)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate files for all resources.

        Args:
            config: The validated kiln configuration.

        Returns:
            List of generated files across all resources.

        """
        files: list[GeneratedFile] = []
        for resource in config.resources:
            files.extend(generate_resource(resource, config))
        return files
