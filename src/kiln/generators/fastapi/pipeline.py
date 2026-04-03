"""Resource generation pipeline.

Composes :class:`~kiln.generators.base.FileSpec` objects and runs
:class:`~kiln.generators.fastapi.operations.Operation` classes
against them to produce the final generated files.

The pipeline is completely generic — it manages a
``dict[str, FileSpec]`` bag.  Operations create and populate
specs by key; the pipeline auto-wires cross-file imports and
renders.

Customise by passing a different list of operations to
:class:`ResourcePipeline`::

    from kiln.generators.fastapi.operations import (
        default_operations,
    )
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    ops = default_operations()
    ops.append(MyBulkCreateOperation())
    pipeline = ResourcePipeline(operations=ops)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators.fastapi.operations import (
    Operation,
    build_shared_context,
    default_operations,
)

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig, ResourceConfig
    from kiln.generators.base import FileSpec, GeneratedFile


class ResourcePipeline:
    """Composable pipeline that builds files for one resource.

    For each resource the pipeline:

    1. Runs every enabled :class:`Operation` against a shared
       ``specs`` dict, letting each create specs or append
       content to existing ones.
    2. Auto-wires cross-file imports — every spec's exports
       are made available to every other spec.
    3. Renders each spec to a :class:`GeneratedFile`.

    Args:
        operations: Ordered list of operations to run.  Defaults
            to :func:`default_operations`.

    """

    def __init__(  # noqa: D107
        self,
        operations: list[Operation] | None = None,
    ) -> None:
        self.operations = (
            operations if operations is not None else default_operations()
        )

    def build(
        self,
        resource: ResourceConfig,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Build all generated files for a single *resource*.

        Args:
            resource: The resource configuration.
            config: The top-level kiln configuration.

        Returns:
            List of :class:`GeneratedFile` objects.

        """
        ctx = build_shared_context(resource, config)
        specs: dict[str, FileSpec] = {}

        # Run operations
        for op in self.operations:
            if op.enabled(resource):
                op.contribute(specs, resource, ctx)

        # Auto-wire cross-file imports
        _wire_imports(specs)

        # Render all specs in insertion order
        return [spec.render() for spec in specs.values()]


def _wire_imports(specs: dict[str, FileSpec]) -> None:
    """Wire cross-file imports between all specs.

    For every pair of specs, if one spec has exports,
    every *other* spec gets an import line for those
    exports.  The ``"serializer"`` spec is special-cased:
    it only imports the ``Resource`` class from the schema,
    not all schema exports.
    """
    spec_list = list(specs.items())
    for src_key, src_spec in spec_list:
        if not src_spec.exports:
            continue
        for dst_key, dst_spec in spec_list:
            if dst_key == src_key:
                continue
            # Serializer only needs the Resource class
            if dst_key == "serializer":
                resource_cls = dst_spec.context["model_name"] + "Resource"
                if resource_cls in src_spec.exports:
                    dst_spec.imports.add_from(src_spec.module, resource_cls)
            else:
                dst_spec.imports.add_from(src_spec.module, *src_spec.exports)
