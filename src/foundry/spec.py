"""Generated-file output type."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class GeneratedFile:
    """Immutable final output -- a path and its content.

    Attributes:
        path: Output path relative to the output directory.
        content: File contents as a string.
        if_exists: Per-file write policy honored by
            :func:`foundry.output.write_files`.  ``"overwrite"``
            (the default) always replaces the target on disk --
            the historical behaviour every be scaffold output
            relied on.  ``"skip"`` writes the file only if it
            does not yet exist; right for one-shot scaffolding
            (e.g. be_root's bootstrap) where users edit the
            file post-generation and a re-run should be
            non-destructive.  ``--force`` / ``--force-paths`` on
            the CLI override ``"skip"`` back to ``"overwrite"``
            for the affected files.

    """

    path: str
    content: str
    if_exists: Literal["overwrite", "skip"] = "overwrite"
