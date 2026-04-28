"""Registration of fe as a foundry target.

Exposes ``target``, the :class:`~foundry.target.Target` instance
the foundry CLI picks up via the ``foundry.targets`` entry-point
group declared in fe's ``pyproject.toml``.  At build time foundry
walks ``fe.operations`` (the entry-point group named in
``operations_entry_point`` below) to assemble the per-build
registry of fe's operations.
"""

from pathlib import Path

from fe.config import ProjectConfig
from foundry.target import Target

_HERE = Path(__file__).parent

target = Target(
    name="fe",
    language="",
    schema=ProjectConfig,
    template_dir=_HERE / "templates",
    operations_entry_point="fe.operations",
)
