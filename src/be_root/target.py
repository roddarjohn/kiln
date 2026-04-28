"""Registration of be_root as a foundry target.

Constructs ``target`` and exposes it via the
``foundry.targets`` entry-point group declared in the package's
``pyproject.toml``.  At build time foundry walks
``be_root.operations`` (the entry-point group named in
``operations_entry_point`` below) to assemble the per-build
registry; be_root's ops never end up in be's registry, and
vice versa.
"""

from pathlib import Path

from be_root.config import RootConfig
from foundry.target import Target

_HERE = Path(__file__).parent

target = Target(
    name="be_root",
    language="python",
    schema=RootConfig,
    template_dir=_HERE / "templates",
    operations_entry_point="be_root.operations",
)
