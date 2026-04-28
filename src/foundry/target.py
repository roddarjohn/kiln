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
        operations_entry_point: Entry-point group name where the
            target's ``@operation``-decorated classes are
            registered, e.g. ``"kiln.operations"`` or
            ``"be_root.operations"``.  At build time the
            pipeline calls
            :func:`~foundry.operation.load_registry` against
            this group to assemble a fresh, target-private
            registry; targets installed side-by-side never see
            each other's ops.
        jsonnet_stdlib_dir: Optional directory of jsonnet
            ``.libsonnet`` files exposed to configs as
            ``<name>/...`` imports.  ``None`` when the target
            ships no stdlib.

    """

    name: str
    language: str
    schema: type[FoundryConfig]
    template_dir: Path
    operations_entry_point: str
    jsonnet_stdlib_dir: Path | None = None


def discover_targets() -> list[Target]:
    """Load every :class:`Target` registered under ``foundry.targets``.

    Returns:
        All installed targets, in entry-point discovery order.

    """
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    return [ep.load() for ep in eps]
