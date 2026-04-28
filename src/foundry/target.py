"""Target plugin protocol and discovery.

A *target* is the glue between foundry's generic engine and a
concrete framework (FastAPI + SQLAlchemy in kiln's case).  A
target is pure data:

* the pydantic schema its config files validate against,
* the directory of Jinja templates its renderers reference,
* and an optional directory of jsonnet ``.libsonnet`` files
  configs can import under the ``<name>/...`` prefix.

Everything else -- loading, engine, registry, assembler, and
output -- lives in foundry.  Targets are discovered at CLI
startup via the ``foundry.targets`` entry-point group.  The CLI
auto-selects the only installed target, or routes by
``--target`` when multiple are present.
"""

import importlib.metadata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from foundry.config import FoundryConfig
    from foundry.operation import OperationRegistry


ENTRY_POINT_GROUP = "foundry.targets"


@dataclass(frozen=True)
class Target:
    """A concrete code-generation target.

    Attributes:
        name: Short identifier, used for ``--target`` dispatch
            when multiple targets are installed and as the jsonnet
            stdlib import prefix.
        language: Language-identifier the target generates for
            (e.g. ``"python"``).  Passed to
            :func:`foundry.imports.format_imports` so the
            assembler renders import blocks in the right syntax.
            Targets declare their formatter under the
            ``foundry.import_formatters`` entry-point group.
        schema: :class:`~foundry.config.FoundryConfig` subclass the
            target's config files validate against.  Foundry's
            loader instantiates this.
        template_dir: Directory of Jinja templates the target's
            renderers reference.  Foundry builds the Jinja
            environment rooted here.
        jsonnet_stdlib_dir: Optional directory of jsonnet
            ``.libsonnet`` files exposed to configs as
            ``<name>/...`` imports.  ``None`` when the target
            ships no stdlib.
        registry: Dedicated operation registry holding the ops
            this target wants the engine to run.  Each target
            owns its own registry: targets that ship together
            (e.g. ``kiln`` and ``kiln_root``) declare overlapping
            scope names but their ops would crash on each other's
            configs, so foundry never mixes them.  Populate by
            having the target's package import its op modules at
            the same time it constructs the :class:`Target` --
            the :func:`~foundry.operation.operation` decorator
            then pushes each op into whichever registry the
            module passed it.

    """

    name: str
    language: str
    schema: type[FoundryConfig]
    template_dir: Path
    registry: OperationRegistry
    jsonnet_stdlib_dir: Path | None = None


def discover_targets() -> list[Target]:
    """Load every :class:`Target` registered under ``foundry.targets``.

    Returns:
        All installed targets, in entry-point discovery order.

    """
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    return [ep.load() for ep in eps]
