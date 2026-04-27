"""Registration of kiln as a foundry target.

Exposes ``target``, the :class:`~foundry.target.Target` instance
the foundry CLI picks up via the ``foundry.targets`` entry-point
group declared in kiln's ``pyproject.toml``.

Importing this module transitively imports kiln's renderer and
operation modules (via foundry's entry-point discovery), which
populate :data:`foundry.render.registry` as a side effect.
"""

from __future__ import annotations

from pathlib import Path

from foundry.target import Target
from kiln.config.schema import ProjectConfig

_HERE = Path(__file__).parent

target = Target(
    name="kiln",
    language="python",
    schema=ProjectConfig,
    template_dir=_HERE / "templates",
    jsonnet_stdlib_dir=_HERE / "jsonnet",
)
