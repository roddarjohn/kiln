"""Registration of kiln as a foundry target.

Exposes ``target``, the :class:`~foundry.target.Target` instance
the foundry CLI picks up via the ``foundry.targets`` entry-point
group declared in kiln's ``pyproject.toml``.  At build time
foundry walks ``kiln.operations`` (the entry-point group named
in ``operations_entry_point`` below) to assemble the per-build
registry of kiln's operations, and importing each entry-point
class populates :data:`foundry.render.registry` as a side
effect.
"""

from pathlib import Path

from foundry.target import Target
from kiln.config.schema import ProjectConfig

_HERE = Path(__file__).parent

target = Target(
    name="kiln",
    language="python",
    schema=ProjectConfig,
    template_dir=_HERE / "templates",
    operations_entry_point="kiln.operations",
    jsonnet_stdlib_dir=_HERE / "jsonnet",
)
