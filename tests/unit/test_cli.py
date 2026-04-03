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
# generate — error handling
# ---------------------------------------------------------------------------


def test_generate_bad_config_exits_1(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1")
    result = runner.invoke(
        app, ["generate", "--config", str(bad), "--out", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "Error" in result.output


def _write_json_config(tmp_path: Path, data: dict) -> Path:
    cfg = tmp_path / "kiln.json"
    cfg.write_text(json.dumps(data))
    return cfg


# ---------------------------------------------------------------------------
# generate — app mode (no apps list)
# ---------------------------------------------------------------------------


def test_generate_writes_app_files(tmp_path: Path):
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
                }
            ],
            "routes": [{"type": "crud", "model": "Post", "crud": {}}],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["generate", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 0
    assert "Generated" in result.output
    assert (out / "myapp" / "models" / "post.py").exists()
    assert (out / "myapp" / "routes" / "post.py").exists()
    assert (out / "myapp" / "schemas" / "post.py").exists()


def test_generate_with_auth_writes_scaffold(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "auth": {"type": "jwt"},
            "databases": [{"key": "primary", "default": True}],
            "models": [],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["generate", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 0
    assert (out / "db" / "base.py").exists()
    assert (out / "db" / "primary_session.py").exists()
    assert (out / "auth" / "dependencies.py").exists()


def test_generate_overwrites_on_rerun(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "auth": {"type": "jwt"},
            "models": [],
        },
    )
    out = tmp_path / "out"
    runner.invoke(app, ["generate", "--config", str(cfg), "--out", str(out)])
    sentinel = out / "auth" / "dependencies.py"
    sentinel.write_text("# modified")
    runner.invoke(app, ["generate", "--config", str(cfg), "--out", str(out)])
    assert sentinel.read_text() != "# modified"


def test_generate_no_validate_flag_accepted(tmp_path: Path):
    """--no-validate is accepted for backwards compatibility."""
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "views": [
                {
                    "name": "my_view",
                    "returns": [{"name": "id", "type": "uuid"}],
                }
            ],
            "routes": [{"type": "view", "view": "my_view"}],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["generate", "--config", str(cfg), "--out", str(out), "--no-validate"],
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# generate — project mode (apps list)
# ---------------------------------------------------------------------------


def test_generate_project_mode_writes_all_apps(tmp_path: Path):
    blog_cfg = tmp_path / "blog.json"
    blog_cfg.write_text(
        json.dumps(
            {
                "module": "blog",
                "models": [
                    {
                        "name": "Article",
                        "table": "articles",
                        "fields": [
                            {"name": "id", "type": "uuid", "primary_key": True}
                        ],
                    }
                ],
                "routes": [{"type": "crud", "model": "Article", "crud": {}}],
            }
        )
    )
    inv_cfg = tmp_path / "inventory.json"
    inv_cfg.write_text(
        json.dumps(
            {
                "module": "inventory",
                "models": [
                    {
                        "name": "Product",
                        "table": "products",
                        "fields": [
                            {"name": "id", "type": "uuid", "primary_key": True}
                        ],
                    }
                ],
                "routes": [{"type": "crud", "model": "Product", "crud": {}}],
            }
        )
    )
    project_cfg = _write_json_config(
        tmp_path,
        {
            "auth": {"type": "jwt"},
            "databases": [{"key": "primary", "default": True}],
            "apps": [
                {"config": json.loads(blog_cfg.read_text()), "prefix": "/blog"},
                {
                    "config": json.loads(inv_cfg.read_text()),
                    "prefix": "/inventory",
                },
            ],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["generate", "--config", str(project_cfg), "--out", str(out)]
    )
    assert result.exit_code == 0
    assert (out / "db" / "primary_session.py").exists()
    assert (out / "auth" / "dependencies.py").exists()
    assert (out / "blog" / "models" / "article.py").exists()
    assert (out / "inventory" / "models" / "product.py").exists()
    assert (out / "routes" / "__init__.py").exists()
    root_router = (out / "routes" / "__init__.py").read_text()
    assert "blog_router" in root_router
    assert "inventory_router" in root_router


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
