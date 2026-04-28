"""Registration of fe_root as a foundry target.

Constructs :data:`target` and exposes it via the
``foundry.targets`` entry-point group declared in the package's
``pyproject.toml``.  At build time foundry walks
``fe_root.operations`` (the entry-point group named in
``operations_entry_point`` below) to assemble the per-build
registry; fe_root's ops never end up in any other target's
registry.
"""

from pathlib import Path

from fe_root.config import RootConfig
from foundry.target import Target

_HERE = Path(__file__).parent

target = Target(
    name="fe_root",
    language="",
    schema=RootConfig,
    template_dir=_HERE / "templates",
    operations_entry_point="fe_root.operations",
)
