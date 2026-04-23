"""Target-agnostic build-phase output types.

Only :class:`StaticFile` is truly target-neutral and lives here;
Python / FastAPI / Pydantic output types live in :mod:`kiln.outputs`
since a non-Python target wouldn't use them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StaticFile:
    """A file rendered directly from a template.

    Used for scaffold files (auth, db sessions), utils, and other
    files that don't need the assembler's multi-contributor merging.
    """

    path: str
    template: str
    context: dict[str, Any] = field(default_factory=dict)
