"""Kiln playground runner.

Evaluates a Jsonnet config, runs all generators, and writes the
output to playground/generated/<config-stem>/.

Usage::

    # run the default example
    uv run --group playground python playground/run_example.py

    # run a specific example
    uv run --group playground python playground/run_example.py examples/blog.jsonnet

The generated/ directory is created fresh on each run.
All files are always overwritten on each run.
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

_PLAYGROUND = Path(__file__).parent
_DEFAULT_CONFIG = _PLAYGROUND / "example.jsonnet"


def run(config_path: Path) -> None:
    """Run generators for *config_path* and write output under generated/."""
    out = _PLAYGROUND / "generated" / config_path.stem
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    config = load(config_path)
    registry = GeneratorRegistry.default()
    files = registry.run(config)

    written = 0
    for f in files:
        target = out / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        written += 1
        print(f"  wrote  {f.path}")

    print(f"\n{written} file(s) written.")
    print(f"Output: {out}\n")


def main() -> None:
    """Run one or all playground examples."""
    if len(sys.argv) > 1:
        configs = [_PLAYGROUND / sys.argv[1]]
    else:
        # Run all .jsonnet files: the root example plus everything in examples/
        configs = sorted(
            [_DEFAULT_CONFIG]
            + list((_PLAYGROUND / "examples").glob("*.jsonnet"))
        )

    for cfg in configs:
        print(f"=== {cfg.relative_to(_PLAYGROUND)} ===")
        run(cfg)


if __name__ == "__main__":
    main()
