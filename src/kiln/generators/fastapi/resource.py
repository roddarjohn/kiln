"""Generator that produces FastAPI schema and route files for resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators.fastapi.pipeline import ResourcePipeline

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig
    from kiln.generators.base import GeneratedFile


class ResourceGenerator:
    """Produces schema, serializer, and route files per resource.

    For each resource up to three files are emitted:

    * ``{module}/schemas/{model}.py`` -- Pydantic request/response
      schemas.
    * ``{module}/serializers/{model}.py`` -- serializer function
      that converts an ORM model instance to the resource schema
      (only when a resource schema is generated).
    * ``{module}/routes/{model}.py`` -- async FastAPI route handlers
      using SQLAlchemy ``select``, ``insert``, ``update``,
      ``delete``.

    Generated files are always overwritten on re-generation.

    The pipeline can be customised by passing a
    :class:`~kiln.generators.fastapi.pipeline.ResourcePipeline`
    with a different set of operations.
    """

    def __init__(  # noqa: D107
        self,
        pipeline: ResourcePipeline | None = None,
    ) -> None:
        self.pipeline = pipeline or ResourcePipeline()

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "resources"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when the config defines at least one resource.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.resources)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate schema, serializer, and route files per resource.

        Args:
            config: The validated kiln configuration.

        Returns:
            Up to three :class:`~kiln.generators.base.GeneratedFile`
            instances per resource.

        """
        files: list[GeneratedFile] = []
        for resource in config.resources:
            files.extend(self.pipeline.build(resource, config))
        return files
