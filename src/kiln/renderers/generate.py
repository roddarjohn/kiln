"""New-protocol generation entry point.

Config in, files out.  Runs the :class:`~foundry.engine.Engine`
over the full config tree in a single pass, then assembles the
build store into files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.engine import Engine
from foundry.render import RenderCtx
from kiln.errors import GenerationError
from kiln.generators._env import env
from kiln.renderers import registry
from kiln.renderers.assembler import assemble

if TYPE_CHECKING:
    from foundry.spec import GeneratedFile
    from kiln.config.schema import ProjectConfig


def generate(config: ProjectConfig) -> list[GeneratedFile]:
    """Generate all files from a kiln config.

    :class:`~foundry.engine.Engine` auto-discovers operations from
    the ``foundry.operations`` entry-point group, then runs the
    hierarchical engine once over the full config tree, and
    assembles the resulting build store into files.

    Args:
        config: The validated kiln configuration.

    Returns:
        Flat list of all generated files.

    Raises:
        GenerationError: If config semantics are invalid (e.g. a
            resource references an unknown operation, or an
            operation's options fail introspection).

    """
    try:
        engine = Engine(package_prefix=config.package_prefix)

        store = engine.build(config)

        ctx = RenderCtx(
            env=env,
            config=config,
            package_prefix=config.package_prefix,
        )

        return assemble(store, registry, ctx)

    except ValueError as exc:
        raise GenerationError(str(exc)) from exc
