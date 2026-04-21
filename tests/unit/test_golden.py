"""Golden-file regression test for kiln's full generation pipeline.

Runs the CLI against a representative config and asserts byte-for-byte
equality with the checked-in snapshot under ``tests/unit/golden/``.

If you intentionally change generator output, regenerate the snapshot::

    uv run python scripts/capture_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kiln.cli import app

runner = CliRunner()

GOLDEN_CONFIG: dict = {
    "module": "myapp",
    "auth": {
        "type": "jwt",
        "verify_credentials_fn": "myapp.auth.verify",
    },
    "databases": [{"key": "primary", "default": True}],
    "resources": [
        {
            "model": "myapp.models.Post",
            "operations": [
                {
                    "name": "get",
                    "fields": [
                        {"name": "title", "type": "str"},
                        {"name": "views", "type": "int"},
                    ],
                },
                {
                    "name": "list",
                    "fields": [
                        {"name": "title", "type": "str"},
                        {"name": "views", "type": "int"},
                    ],
                    "filters": {"fields": ["title"]},
                    "ordering": {
                        "fields": ["title", "views"],
                        "default": "title",
                    },
                    "pagination": {"mode": "offset"},
                },
                {
                    "name": "create",
                    "fields": [{"name": "title", "type": "str"}],
                },
                {
                    "name": "update",
                    "fields": [{"name": "title", "type": "str"}],
                },
                "delete",
            ],
        }
    ],
}

GOLDEN_DIR = Path(__file__).parent / "golden"


def _collect(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(root))] = path.read_text()
    return files


def test_generated_output_matches_golden(tmp_path: Path) -> None:
    cfg = tmp_path / "kiln.json"
    cfg.write_text(json.dumps(GOLDEN_CONFIG))
    out = tmp_path / "out"

    result = runner.invoke(
        app, ["generate", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output

    actual = _collect(out)
    expected = _collect(GOLDEN_DIR)

    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    assert not missing, f"missing generated files: {sorted(missing)}"
    assert not extra, f"unexpected generated files: {sorted(extra)}"

    diffs: list[str] = [
        name for name in sorted(expected) if actual[name] != expected[name]
    ]
    if diffs:
        first = diffs[0]
        pytest.fail(
            f"generated output drifted for {len(diffs)} file(s); "
            f"first diff in '{first}':\n"
            f"--- golden\n{expected[first]}\n"
            f"+++ actual\n{actual[first]}"
        )
