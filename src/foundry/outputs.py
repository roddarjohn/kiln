"""Target-agnostic build-phase output types.

Only :class:`~foundry.outputs.StaticFile` is truly target-neutral
and lives here; Python / FastAPI / Pydantic output types live in
:mod:`kiln.outputs` since a non-Python target wouldn't use them.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from foundry.render import FileFragment, Fragment, RenderCtx, registry


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


@registry.renders(StaticFile)
def _static_fragment(sf: StaticFile, _ctx: RenderCtx) -> Iterator[Fragment]:
    """Render a :class:`StaticFile` into a single :class:`FileFragment`.

    Lives in foundry (next to :class:`StaticFile`) rather than in
    a target package so every target gets the renderer the moment
    it imports :mod:`foundry.outputs` -- without this, a target
    that doesn't transitively import kiln's renderer module would
    fail with ``LookupError: No renderer for StaticFile`` the
    first time an op yields one.
    """
    yield FileFragment(
        path=sf.path,
        template=sf.template,
        context=dict(sf.context),
        if_exists=sf.if_exists,
    )
