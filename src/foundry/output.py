"""File output helpers for writing generated files to disk."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from foundry.spec import GeneratedFile


def write_files(
    files: Sequence[GeneratedFile],
    out_dir: Path,
    *,
    force: bool = False,
    force_paths: Iterable[str] | None = None,
) -> int:
    """Write generated files to disk, honoring per-file write policy.

    Each file's :attr:`~foundry.spec.GeneratedFile.path` is joined
    with *out_dir* to determine the target path.  Parent
    directories are created as needed.

    The :attr:`~foundry.spec.GeneratedFile.if_exists` policy
    decides what happens when the target already exists:

    * ``"overwrite"`` (default for kiln output) -- replace
      unconditionally.
    * ``"skip"`` (kiln_root's bootstrap files) -- leave the
      existing file untouched.

    *force* and *force_paths* let the caller override ``"skip"``
    back to ``"overwrite"`` from the CLI without changing the
    file declarations themselves:

    * ``force=True`` clobbers every ``"skip"`` file.
    * ``force_paths={"main.py", "pyproject.toml"}`` clobbers only
      those paths -- handy for resetting a single bootstrapped
      file without touching the rest.

    Args:
        files: Sequence of :class:`~foundry.spec.GeneratedFile`
            objects.
        out_dir: Root directory for output paths.
        force: When ``True``, treat every file as
            ``"overwrite"`` regardless of its declared policy.
        force_paths: Optional collection of paths (relative to
            *out_dir*, matching :attr:`GeneratedFile.path`) whose
            ``"skip"`` declaration should be overridden to
            ``"overwrite"``.  Ignored when *force* is ``True``.

    Returns:
        Number of files written (skipped files do not count).

    """
    forced = set(force_paths or ())
    written = 0

    for f in files:
        target = out_dir / f.path

        should_overwrite = (
            f.if_exists == "overwrite" or force or f.path in forced
        )

        if not should_overwrite and target.exists():
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        written += 1

    return written
