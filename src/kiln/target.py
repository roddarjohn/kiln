"""Registration of kiln as a foundry target.

Exposes ``target``, the :class:`~foundry.target.Target` instance
the foundry CLI picks up via the ``foundry.targets`` entry-point
group declared in kiln's ``pyproject.toml``.

Importing this module transitively imports kiln's renderer and
operation modules (via :func:`foundry.operation.load_default_registry`,
which walks the ``foundry.operations`` entry-point group), which
populate :data:`foundry.render.registry` as a side effect.  The
kiln target hands the populated default registry to foundry so
the engine sees every kiln op.  Targets that need to stay
isolated from kiln's ops (e.g. ``kiln_root``) construct their
own registry instead.
"""

from pathlib import Path

from foundry.operation import load_default_registry
from foundry.target import Target
from kiln.config.schema import ProjectConfig

_HERE = Path(__file__).parent

target = Target(
    name="kiln",
    language="python",
    schema=ProjectConfig,
    template_dir=_HERE / "templates",
    registry=load_default_registry(),
    jsonnet_stdlib_dir=_HERE / "jsonnet",
)
