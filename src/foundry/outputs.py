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

    Attributes:
        path: Output path relative to the output directory.
        template: Jinja template name (relative to the target's
            template directory).
        context: Template variables.
        if_exists: Write policy honored by
            :func:`foundry.output.write_files`.  Defaults to
            ``"overwrite"`` (the historical behaviour every kiln
            scaffold output relied on); kiln_root flips this to
            ``"skip"`` so re-bootstrapping is non-destructive
            unless the user passes ``--force`` /
            ``--force-paths``.

    """

    path: str
    template: str
    context: dict[str, Any] = field(default_factory=dict)
    if_exists: Literal["overwrite", "skip"] = "overwrite"
