"""Kiln playground runner.

Evaluates example.jsonnet, runs all generators, and writes the
output to playground/generated/.

Usage::

    uv run --group playground python playground/run_example.py

The generated/ directory is created fresh on each run.
Files with ``overwrite=False`` (pgcraft stubs) are only written once.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Allow running without installing kiln as a package.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kiln.config.loader import load  # noqa: E402
from kiln.generators.registry import GeneratorRegistry  # noqa: E402

_CONFIG = Path(__file__).parent / "example.jsonnet"
_OUT = Path(__file__).parent / "generated"


def main() -> None:
    """Run the playground example and print a summary."""
    # Fresh output directory each run (respects overwrite=False logic).
    if _OUT.exists():
        shutil.rmtree(_OUT)
    _OUT.mkdir()

    config = load(_CONFIG)
    registry = GeneratorRegistry.default()
    files = registry.run(config)

    written = 0
    skipped = 0
    for f in files:
        target = _OUT / f.path
        if target.exists() and not f.overwrite:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        written += 1
        print(f"  wrote  {f.path}")

    print(f"\n{written} file(s) written, {skipped} stub(s) skipped.")
    print(f"Output: {_OUT}")


if __name__ == "__main__":
    main()
