"""Generic build pipeline: config in, files out.

Runs the :class:`~foundry.engine.Engine` over the full config
tree in a single pass, then assembles the build store into files
using foundry's shared registry and generic assembler.  Reads
:attr:`~foundry.config.FoundryConfig.package_prefix` directly from
the config and does not inspect any target-specific field.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from foundry.assembler import assemble
from foundry.engine import Engine
from foundry.env import create_jinja_env
from foundry.errors import GenerationError
from foundry.operation import load_registry
from foundry.render import RenderCtx, registry

if TYPE_CHECKING:
    from foundry.config import FoundryConfig
    from foundry.spec import GeneratedFile
    from foundry.target import Target

_FOUNDRY_TEMPLATES = Path(__file__).parent / "templates"


def generate(config: FoundryConfig, target: Target) -> list[GeneratedFile]:
    """Generate all files for a validated *config*.

    :class:`~foundry.engine.Engine` runs the hierarchical engine
    once over the full config tree, then foundry's generic
    assembler turns the resulting build store into files.

    Operation discovery is target-scoped:
    :func:`~foundry.operation.load_registry` walks the target's
    ``operations_entry_point`` group at build time and assembles a
    fresh registry from the classes declared there.  Targets
    installed side-by-side never see each other's ops.

    Args:
        config: Validated config model.
        target: The selected target; its ``template_dir`` builds
            the Jinja environment, and its
            ``operations_entry_point`` drives the per-build
            registry.

    Returns:
        Flat list of generated files.

    Raises:
        GenerationError: If config semantics are invalid (e.g. a
            resource references an unknown operation, or an
            operation's options fail introspection).

    """
    env = create_jinja_env(target.template_dir, _FOUNDRY_TEMPLATES)
    op_registry = load_registry(target.operations_entry_point)

    try:
        engine = Engine(
            registry=op_registry,
            package_prefix=config.package_prefix,
        )
        store = engine.build(config)

        ctx = RenderCtx(
            env=env,
            config=config,
            package_prefix=config.package_prefix,
            language=target.language,
        )

        return assemble(store, registry, ctx)

    except ValueError as exc:
        raise GenerationError(str(exc)) from exc
