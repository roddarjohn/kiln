"""File output helpers for writing generated files to disk."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from foundry.spec import GeneratedFile


def write_files(
    files: Sequence[GeneratedFile],
    out_dir: Path,
) -> int:
    """Write generated files to disk.

    Each file's :attr:`~foundry.spec.GeneratedFile.path` is joined
    with *out_dir* to determine the target path.  Parent directories
    are created as needed.  Existing files are always overwritten.

    Args:
        files: Sequence of :class:`~foundry.spec.GeneratedFile` objects.
        out_dir: Root directory for output paths.

    Returns:
        Number of files written.

    """
    written = 0

    for f in files:
        target = out_dir / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        written += 1

    return written
