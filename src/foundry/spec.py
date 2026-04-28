"""Generated-file output type."""

from dataclasses import dataclass
from typing import Literal

WriteMode = Literal["overwrite", "skip"]
"""Per-file write policy honored by :func:`foundry.output.write_files`.

* ``"overwrite"`` -- always replace the target on disk.  Right
  for files the generator owns end-to-end (e.g. kiln's regenerated
  routes/schemas).
* ``"skip"`` -- write the file only if it does not yet exist.
  Right for one-shot scaffolding (e.g. kiln_root's bootstrap)
  where users edit the file post-generation and a re-run should
  be non-destructive.  ``--force`` / ``--force-paths`` on the CLI
  override this back to "overwrite" for the affected files.
"""


@dataclass(frozen=True)
class GeneratedFile:
    """Immutable final output -- a path and its content.

    Attributes:
        path: Output path relative to the output directory.
        content: File contents as a string.
        if_exists: Write policy when the target already exists on
            disk.  Defaults to ``"overwrite"`` to preserve the
            historical behaviour every kiln output relied on
            before the policy was introduced.

    """

    path: str
    content: str
    if_exists: WriteMode = "overwrite"
