"""Tests for the CLI entry point."""

import json
from pathlib import Path

from typer.testing import CliRunner

from kiln.cli import _write_files, app
from kiln.generators.base import GeneratedFile

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "autogenerating" in result.output


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------


def test_init_writes_scaffold(tmp_path: Path):
    result = runner.invoke(app, ["init", "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert "Scaffold written" in result.output
    assert (tmp_path / "db" / "base.py").exists()
    assert (tmp_path / "auth" / "dependencies.py").exists()


def test_init_does_not_overwrite(tmp_path: Path):
    # First run writes files.
    runner.invoke(app, ["init", "--out", str(tmp_path)])
    sentinel = tmp_path / "db" / "base.py"
    original = sentinel.read_text()
    sentinel.write_text("# modified")
    # Second run must not overwrite.
    runner.invoke(app, ["init", "--out", str(tmp_path)])
    assert sentinel.read_text() == "# modified"
    _ = original  # silence unused-variable warning


# ---------------------------------------------------------------------------
# generate command
# ---------------------------------------------------------------------------


def _write_json_config(tmp_path: Path, data: dict) -> Path:
    cfg = tmp_path / "kiln.json"
    cfg.write_text(json.dumps(data))
    return cfg


def test_generate_bad_config_exits_1(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1")
    result = runner.invoke(
        app, ["generate", "--config", str(bad), "--out", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "Error" in result.output


def test_generate_writes_files(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "models": [
                {
                    "name": "Post",
                    "table": "posts",
                    "fields": [
                        {"name": "id", "type": "uuid", "primary_key": True},
                        {"name": "title", "type": "str"},
                    ],
                    "crud": {},
                }
            ],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["generate", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 0
    assert "Generated" in result.output
    assert (out / "models" / "post.py").exists()
    assert (out / "routes" / "post.py").exists()
    assert (out / "schemas" / "post.py").exists()


def test_generate_no_validate_skips_query_fn_check(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "views": [
                {
                    "name": "my_view",
                    "model": "Thing",
                    "query_fn": "does.not.exist.get_query",
                    "parameters": [],
                    "returns": [{"name": "id", "type": "uuid"}],
                }
            ],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["generate", "--config", str(cfg), "--out", str(out), "--no-validate"],
    )
    assert result.exit_code == 0


def test_generate_validates_query_fn_by_default(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "views": [
                {
                    "name": "my_view",
                    "model": "Thing",
                    "query_fn": "does.not.exist.get_query",
                    "parameters": [],
                    "returns": [{"name": "id", "type": "uuid"}],
                }
            ],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["generate", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 1
    assert "query_fn" in result.output


# ---------------------------------------------------------------------------
# _write_files
# ---------------------------------------------------------------------------


def test_write_files_skips_no_overwrite(tmp_path: Path):
    f = GeneratedFile(path="foo.py", content="# original", overwrite=False)
    _write_files([f], tmp_path)
    (tmp_path / "foo.py").write_text("# modified")
    written, skipped = _write_files([f], tmp_path)
    assert written == 0
    assert skipped == 1


def test_write_files_overwrites_by_default(tmp_path: Path):
    f = GeneratedFile(path="bar.py", content="# v1")
    _write_files([f], tmp_path)
    f2 = GeneratedFile(path="bar.py", content="# v2")
    written, skipped = _write_files([f2], tmp_path)
    assert written == 1
    assert skipped == 0
    assert (tmp_path / "bar.py").read_text() == "# v2"


def test_write_files_creates_subdirs(tmp_path: Path):
    f = GeneratedFile(path="a/b/c.py", content="x")
    written, _ = _write_files([f], tmp_path)
    assert written == 1
    assert (tmp_path / "a" / "b" / "c.py").exists()
