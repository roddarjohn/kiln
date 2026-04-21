"""Target plugin protocol and discovery.

A *target* is the glue between foundry's generic engine and a
concrete framework (FastAPI + SQLAlchemy in kiln's case).  Each
target packages the three things the foundry CLI needs:

* a config loader (path → validated pydantic model),
* a ``generate`` callable (config → list of files),
* a default-output-directory policy.

Targets are discovered at CLI startup via the ``foundry.targets``
entry-point group.  The CLI auto-selects the only installed
target, or routes by ``--target`` when multiple are present.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from foundry.spec import GeneratedFile


ENTRY_POINT_GROUP = "foundry.targets"


@dataclass(frozen=True)
class Target:
    """A concrete code-generation target.

    Attributes:
        name: Short identifier, used for ``--target`` dispatch
            when multiple targets are installed.
        load_config: Parse and validate a config file from a
            :class:`~pathlib.Path`.  Should raise
            :class:`~foundry.errors.CLIError` (or a subclass) on
            bad input so the CLI renders it cleanly.
        generate: Turn a validated config model into a flat list
            of :class:`~foundry.spec.GeneratedFile` objects.
        default_out: Compute the default output directory from
            the validated config.  Return ``None`` when the config
            does not imply a default and the CLI should fall back
            to the current directory.  ``None`` for the field
            itself means the target has no default policy at all.

    """

    name: str
    load_config: Callable[[Path], BaseModel]
    generate: Callable[[BaseModel], list[GeneratedFile]]
    default_out: Callable[[BaseModel], Path | None] | None = None


def discover_targets() -> list[Target]:
    """Load every :class:`Target` registered under ``foundry.targets``.

    Returns:
        All installed targets, in entry-point discovery order.

    """
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    return [ep.load() for ep in eps]
