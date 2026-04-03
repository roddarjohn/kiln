"""Resource generation pipeline.

Composes :class:`~kiln.generators.base.FileSpec` objects and runs
:class:`~kiln.generators.fastapi.operations.Operation` classes
against them to produce the final generated files.

Operations are resolved from configuration via
:class:`~kiln.generators.fastapi.operations.OperationRegistry`,
which discovers operations from ``kiln.operations`` entry points.

The pipeline is completely generic — it manages a
``dict[str, FileSpec]`` bag.  Operations create and populate
specs by key; the pipeline auto-wires cross-file imports and
renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.config.schema import OperationConfig
from kiln.generators.fastapi.operations import (
    Operation,
    OperationRegistry,
    SetupOperation,
    build_shared_context,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from kiln.config.schema import KilnConfig, ResourceConfig
    from kiln.generators.base import FileSpec, GeneratedFile


class ResourcePipeline:
    """Composable pipeline that builds files for one resource.

    For each resource the pipeline:

    1. Resolves operation configs from resource/config inheritance.
    2. Validates all operations before any generation begins.
    3. Runs every operation against a shared ``specs`` dict.
    4. Auto-wires cross-file imports.
    5. Renders each spec to a :class:`GeneratedFile`.

    Args:
        registry: Operation registry for resolving operation
            names to classes.  Defaults to entry-point discovery.

    """

    def __init__(  # noqa: D107
        self,
        registry: OperationRegistry | None = None,
    ) -> None:
        self.registry = registry or OperationRegistry.default()

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
        op_configs = _resolve_op_configs(resource, config)
        ctx = build_shared_context(resource, config, op_configs)
        specs: dict[str, FileSpec] = {}

        # Always run setup (internal, not user-configurable)
        setup_config = OperationConfig(name="setup")
        setup_op = SetupOperation()
        setup_opts = setup_op.Options()
        setup_op.contribute(specs, resource, ctx, setup_config, setup_opts)

        # Pass 1: resolve and parse options (Pydantic validation)
        resolved: list[tuple[Operation, OperationConfig, BaseModel]] = []
        for oc in op_configs:
            op = self.registry.resolve(oc.name, oc.options)
            parsed = op.Options(**oc.options)
            resolved.append((op, oc, parsed))

        # Pass 2: contribute
        for op, oc, parsed in resolved:
            op.contribute(specs, resource, ctx, oc, parsed)

        # Auto-wire cross-file imports
        _wire_imports(specs)

        # Render all specs in insertion order
        return [spec.render() for spec in specs.values()]


def _resolve_op_configs(
    resource: ResourceConfig,
    config: KilnConfig,
) -> list[OperationConfig]:
    """Resolve operation configs with three-level inheritance.

    Resource-level operations override app/config-level.  String
    entries are normalised to :class:`OperationConfig`.

    Args:
        resource: The resource configuration.
        config: The app/project-level configuration.

    Returns:
        List of normalised :class:`OperationConfig` objects.

    """
    raw = (
        resource.operations
        if resource.operations is not None
        else config.operations or []
    )
    return [_normalize_entry(entry) for entry in raw]


def _normalize_entry(
    entry: str | OperationConfig,
) -> OperationConfig:
    """Normalise a string or OperationConfig to OperationConfig."""
    if isinstance(entry, str):
        return OperationConfig(name=entry)
    return entry


def _wire_imports(specs: dict[str, FileSpec]) -> None:
    """Wire cross-file imports between specs.

    Each spec can import from specs that appear **before** it
    in insertion order.  This avoids circular imports — the
    first spec (typically ``"schema"``) never receives wired
    imports.

    The ``"serializer"`` spec is special-cased: it only
    imports the ``Resource`` class, not all schema exports.
    """
    spec_list = list(specs.items())
    for i, (dst_key, dst_spec) in enumerate(spec_list):
        for _src_key, src_spec in spec_list[:i]:
            if not src_spec.exports:
                continue
            if dst_key == "serializer":
                resource_cls = dst_spec.context["model_name"] + "Resource"
                if resource_cls in src_spec.exports:
                    dst_spec.imports.add_from(src_spec.module, resource_cls)
            else:
                dst_spec.imports.add_from(src_spec.module, *src_spec.exports)
