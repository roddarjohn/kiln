"""Resource generation pipeline.

Given a resource config and its operations, produces the
final generated files.  The pipeline:

1. Sets up base specs (schema, route, serializer, test).
2. Resolves and runs each operation's ``contribute()``.
3. Wires cross-file imports via :func:`kiln_core.wire_exports`.
4. Renders each spec to a :class:`GeneratedFile`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.config.schema import OperationConfig
from kiln.generators._env import env
from kiln.generators.fastapi.operations import (
    build_shared_context,
    resolve_operation,
    setup_specs,
)
from kiln_core import wire_exports

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig, ResourceConfig
    from kiln_core import GeneratedFile


def generate_resource(
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
    specs = setup_specs(resource, ctx)

    for oc in op_configs:
        op = resolve_operation(oc.name, oc.options)
        opts = op.Options(**oc.options)
        op.contribute(specs, resource, ctx, oc, opts)

    wire_exports(specs)
    return [spec.render(env) for spec in specs.values()]


def _resolve_op_configs(
    resource: ResourceConfig,
    config: KilnConfig,
) -> list[OperationConfig]:
    """Resolve operation configs with inheritance.

    Resource-level operations override config-level.  String
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
