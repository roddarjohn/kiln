"""Registration of be as a foundry target.

Exposes ``target``, the :class:`~foundry.target.Target` instance
the foundry CLI picks up via the ``foundry.targets`` entry-point
group declared in be's ``pyproject.toml``.  At build time
foundry walks ``be.operations`` (the entry-point group named
in ``operations_entry_point`` below) to assemble the per-build
registry of be's operations, and importing each entry-point
class populates :data:`foundry.render.registry` as a side
effect.
"""

from pathlib import Path

from foundry.target import Target
from be.config.schema import ProjectConfig

_HERE = Path(__file__).parent

target = Target(
    name="be",
    language="python",
    schema=ProjectConfig,
    template_dir=_HERE / "templates",
    operations_entry_point="be.operations",
    jsonnet_stdlib_dir=_HERE / "jsonnet",
)
