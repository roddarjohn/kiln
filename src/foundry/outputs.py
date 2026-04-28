"""Target-agnostic build-phase output types.

Only :class:`~foundry.outputs.StaticFile` is truly target-neutral
and lives here; Python / FastAPI / Pydantic output types live in
:mod:`kiln.outputs` since a non-Python target wouldn't use them.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class StaticFile:
    """A file rendered directly from a template.

    Used for scaffold files (auth, db sessions), utils, and other
    files that don't need the assembler's multi-contributor merging.

    ``if_exists`` defaults to ``"overwrite"`` (kiln's regenerated
    scaffold behaviour); ``"skip"`` makes
    :func:`foundry.output.write_files` leave existing files alone
    -- right for one-shot bootstraps like kiln_root, where
    ``--force`` / ``--force-paths`` is the explicit opt-in to
    clobber.
    """

    path: str
    template: str
    context: dict[str, Any] = field(default_factory=dict)
    if_exists: Literal["overwrite", "skip"] = "overwrite"
