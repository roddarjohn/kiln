"""New-protocol generation entry point.

Config in, files out.  Runs the :class:`~foundry.engine.Engine`
over the full config tree in a single pass, then assembles the
build store into files.
"""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

from foundry.engine import Engine
from foundry.render import RenderCtx
from kiln.generators._env import env
from kiln.renderers import registry
from kiln.renderers.assembler import assemble

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.spec import GeneratedFile

ENTRY_POINT_GROUP = "kiln.operations"


def generate(config: BaseModel) -> list[GeneratedFile]:
    """Generate all files from a kiln config.

    Discovers operations from entry points, runs the hierarchical
    engine once over the full config tree, and assembles the
    resulting build store into files.

    Args:
        config: The validated kiln configuration.

    Returns:
        Flat list of all generated files.

    """
    operations = _discover_operations()
    pkg = getattr(config, "package_prefix", "")

    engine = Engine(operations=operations, package_prefix=pkg)
    store = engine.build(config)

    ctx = RenderCtx(
        env=env,
        config=config,
        package_prefix=pkg,
    )
    return list(assemble(store, registry, ctx))


def _discover_operations() -> list[type]:
    """Load operation classes from the entry point group.

    Returns:
        List of operation classes.

    """
    ops: list[type] = []
    eps = importlib.metadata.entry_points(
        group=ENTRY_POINT_GROUP,
    )
    for ep in eps:
        cls = ep.load()
        ops.append(cls)
    return ops
