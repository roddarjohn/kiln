"""Registration of kiln_root as a foundry target.

Constructs :data:`target` and exposes it via the
``foundry.targets`` entry-point group declared in the package's
``pyproject.toml``.  At build time foundry walks
``kiln_root.operations`` (the entry-point group named in
``operations_entry_point`` below) to assemble the per-build
registry; kiln_root's ops never end up in kiln's registry, and
vice versa.
"""

from pathlib import Path

from foundry.target import Target
from kiln_root.config import RootConfig

_HERE = Path(__file__).parent

target = Target(
    name="kiln_root",
    language="python",
    schema=RootConfig,
    template_dir=_HERE / "templates",
    operations_entry_point="kiln_root.operations",
)
