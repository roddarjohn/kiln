"""Registration of kiln_root as a foundry target.

Constructs :data:`target` and exposes it via the
``foundry.targets`` entry-point group declared in the package's
``pyproject.toml``.  Importing this module also imports
:mod:`kiln_root.operations`, which fires the
:func:`~foundry.operation.operation` decorator and populates
:data:`~kiln_root.operations.REGISTRY`.  The target hands that
registry to foundry's pipeline, keeping kiln_root's ops out of
the default registry kiln uses.
"""

from pathlib import Path

from foundry.target import Target
from kiln_root.config import RootConfig
from kiln_root.operations import REGISTRY

_HERE = Path(__file__).parent

target = Target(
    name="kiln_root",
    language="python",
    schema=RootConfig,
    template_dir=_HERE / "templates",
    registry=REGISTRY,
)
