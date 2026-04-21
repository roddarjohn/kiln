"""Regenerate the golden snapshot used by ``tests/unit/test_golden.py``.

Run from the repo root::

    uv run python scripts/capture_golden.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from typer.testing import CliRunner

# Ensure repo-local test module is importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kiln.cli import app
from tests.unit.test_golden import GOLDEN_CONFIG, GOLDEN_DIR


def main() -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg = tmp_path / "kiln.json"
        cfg.write_text(json.dumps(GOLDEN_CONFIG))
        out = tmp_path / "out"

        result = runner.invoke(
            app, ["generate", "--config", str(cfg), "--out", str(out)]
        )
        if result.exit_code != 0:
            print(result.output)
            sys.exit(result.exit_code)

        if GOLDEN_DIR.exists():
            shutil.rmtree(GOLDEN_DIR)
        shutil.copytree(out, GOLDEN_DIR)

    files = sorted(
        p.relative_to(GOLDEN_DIR) for p in GOLDEN_DIR.rglob("*") if p.is_file()
    )
    print(f"Wrote {len(files)} golden files to {GOLDEN_DIR}:")
    for f in files:
        print(f"  {f}")


if __name__ == "__main__":
    main()
