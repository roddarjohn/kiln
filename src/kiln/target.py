"""Registration of kiln as a foundry target.

Exposes :data:`target`, the :class:`~foundry.target.Target`
instance the foundry CLI picks up via the ``foundry.targets``
entry-point group declared in kiln's ``pyproject.toml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from foundry.target import Target
from kiln.config.loader import load
from kiln.config.schema import ProjectConfig
from kiln.renderers.generate import generate

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.spec import GeneratedFile


def _generate(cfg: BaseModel) -> list[GeneratedFile]:
    """Narrow to :class:`ProjectConfig` and delegate to kiln's generator.

    The foundry CLI types :attr:`Target.generate` as
    ``Callable[[BaseModel], ...]``; kiln's own loader only ever
    returns :class:`ProjectConfig`, so we narrow here and surface
    a clear :class:`TypeError` if something upstream routes a
    foreign config into this target by mistake.
    """
    if not isinstance(cfg, ProjectConfig):
        msg = f"kiln target requires a ProjectConfig, got {type(cfg).__name__}"
        raise TypeError(msg)
    return generate(cfg)


def _default_out(cfg: BaseModel) -> Path | None:
    """Place generated files under ``config.package_prefix`` by default.

    Falls back to ``None`` (CWD) when the prefix is unset or
    empty, matching the historical kiln CLI behavior.
    """
    prefix = getattr(cfg, "package_prefix", "")
    return Path(prefix) if prefix else None


target = Target(
    name="kiln",
    load_config=load,
    generate=_generate,
    default_out=_default_out,
)
