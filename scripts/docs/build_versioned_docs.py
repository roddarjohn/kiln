"""Build versioned Sphinx docs for all tags and main.

Each ``kiln-v*`` git tag and the ``main`` branch get their own
subdirectory under ``docs/_build/html/``.  A ``versions.json`` file
is written at the root so the sidebar version selector can discover
them.

Tag builds run in parallel and are skipped when the output directory
already exists (cache hit).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "docs" / "_build" / "html"
SPHINX_BUILD = [
    "uv",
    "run",
    "--group",
    "docs",
    "sphinx-build",
    "-b",
    "html",
]
TAG_PREFIX = "kiln-"
MAX_WORKERS = min(4, (os.cpu_count() or 1))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _get_tags() -> list[str]:
    """Return version tags sorted newest-first."""
    raw = _git("tag", "-l", "kiln-v*", "--sort=-version:refname")
    return [t for t in raw.splitlines() if t.strip()]


def _build(source_docs: Path, dest: Path) -> bool:
    """Run sphinx-build.  Returns True on success."""
    dest.mkdir(parents=True, exist_ok=True)
    (source_docs / "_generated").mkdir(exist_ok=True)
    result = subprocess.run(
        [*SPHINX_BUILD, str(source_docs), str(dest)],
        cwd=source_docs.parent,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _build_main() -> None:
    """Build docs from the current working tree as 'main'."""
    print("==> Building main")
    dest = OUTPUT / "main"
    if dest.exists():
        shutil.rmtree(dest)
    if not _build(ROOT / "docs", dest):
        print("ERROR: main build failed", file=sys.stderr)
        sys.exit(1)


def _build_tag(tag: str) -> str | None:
    """Build docs for a single git tag.  Returns label on success."""
    label = tag.removeprefix(TAG_PREFIX)
    dest = OUTPUT / label

    if dest.exists() and (dest / "index.html").exists():
        print(f"==> {label} (cached)")
        return label

    print(f"==> Building {label}")
    tmpdir = tempfile.mkdtemp(prefix=f"kiln-docs-{label}-")
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", tmpdir, tag],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        docs_dir = Path(tmpdir) / "docs"
        if not docs_dir.exists():
            print(f"    Skipping {label}: no docs/ directory")
            return None
        if not _build(docs_dir, dest):
            print(f"    Warning: build failed for {label}, skipping")
            return None
    except subprocess.CalledProcessError as exc:
        print(
            f"    Warning: could not checkout {label}: {exc}",
            file=sys.stderr,
        )
        return None
    else:
        return label
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", tmpdir],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> None:
    """Entry point."""
    OUTPUT.mkdir(parents=True, exist_ok=True)

    _build_main()
    versions = ["main"]

    tags = _get_tags()
    if tags:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_build_tag, tag): tag for tag in tags}
            for future in as_completed(futures):
                label = future.result()
                if label:
                    versions.append(label)

    tag_order = [t.removeprefix(TAG_PREFIX) for t in tags]
    versions.sort(
        key=lambda v: tag_order.index(v) if v in tag_order else -1,
    )

    (OUTPUT / "versions.json").write_text(
        json.dumps(versions, indent=2) + "\n",
    )

    redirect = ROOT / "docs" / "_templates" / "redirect.html"
    shutil.copy(redirect, OUTPUT / "index.html")

    (OUTPUT / ".nojekyll").touch()

    print(f"==> Done. Built versions: {', '.join(versions)}")


if __name__ == "__main__":
    main()
