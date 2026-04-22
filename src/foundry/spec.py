"""Generated-file output type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedFile:
    """Immutable final output -- a path and its content.

    Attributes:
        path: Output path relative to the output directory.
        content: File contents as a string.

    """

    path: str
    content: str
