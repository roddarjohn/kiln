"""Tests for the foundry CLI entry point, backed by the kiln target."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from foundry import GeneratedFile, write_files
from foundry.cli import app, cli_main
from foundry.errors import CLIError, ConfigError

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "code-generation" in result.output


# ---------------------------------------------------------------------------
# generate — error handling
# ---------------------------------------------------------------------------


def test_generate_bad_config_raises_config_error(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1")
    result = runner.invoke(
        app, ["generate", "--config", str(bad), "--out", str(tmp_path)]
    )
    assert isinstance(result.exception, ConfigError)


def test_cli_main_renders_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1")
    monkeypatch.setattr(
        "sys.argv",
        ["foundry", "generate", "--config", str(bad), "--out", str(tmp_path)],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error loading config" in captured.err


def _project_with(
    *,
    module: str = "myapp",
    resources: list[dict] | None = None,
    databases: list[dict] | None = None,
    **extras: object,
) -> dict:
    """Build a canonical project config from per-app fields.

    Keeps tests terse without re-introducing a Pydantic-level
    shorthand — the wrapping into ``apps: [...]`` is explicit in
    one place and visible to readers.
    """
    return {
        "databases": databases or [{"key": "primary", "default": True}],
        "apps": [
            {
                "config": {
                    "module": module,
                    "resources": resources or [],
                },
                "prefix": "",
            }
        ],
        **extras,
    }


def _write_json_config(tmp_path: Path, data: dict) -> Path:
    cfg = tmp_path / "kiln.json"
    cfg.write_text(json.dumps(data))
    return cfg


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_writes_app_files(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        _project_with(
            resources=[
                {
                    "model": "myapp.models.Post",
                    "operations": [
                        {
                            "name": "get",
                            "fields": [{"name": "title", "type": "str"}],
                        },
                        {
                            "name": "list",
                            "fields": [{"name": "title", "type": "str"}],
                        },
                        {
                            "name": "create",
                            "fields": [{"name": "title", "type": "str"}],
                        },
                        {
                            "name": "update",
                            "fields": [{"name": "title", "type": "str"}],
                        },
                        {"name": "delete"},
                    ],
                }
            ],
        ),
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["generate", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 0
    assert "Generated" in result.output
    assert (out / "myapp" / "routes" / "post.py").exists()


def test_generate_with_auth_writes_scaffold(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        _project_with(
            auth={
                "type": "jwt",
                "credentials_schema": "myapp.auth.LoginCredentials",
                "validate_fn": "myapp.auth.validate",
                "get_session_fn": "myapp.auth.get_session",
            },
        ),
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["generate", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 0
    assert (out / "db" / "primary_session.py").exists()
    assert (out / "auth" / "router.py").exists()


def test_generate_overwrites_on_rerun(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        _project_with(
            auth={
                "type": "jwt",
                "credentials_schema": "myapp.auth.LoginCredentials",
                "validate_fn": "myapp.auth.validate",
                "get_session_fn": "myapp.auth.get_session",
            },
        ),
    )
    out = tmp_path / "out"
    runner.invoke(app, ["generate", "--config", str(cfg), "--out", str(out)])
    sentinel = out / "auth" / "router.py"
    sentinel.write_text("# modified")
    runner.invoke(app, ["generate", "--config", str(cfg), "--out", str(out)])
    assert sentinel.read_text() != "# modified"


# ---------------------------------------------------------------------------
# generate — multi-app
# ---------------------------------------------------------------------------


def test_generate_project_mode_writes_all_apps(tmp_path: Path):
    field = [{"name": "title", "type": "str"}]
    blog_app = {
        "module": "blog",
        "resources": [
            {
                "model": "blog.models.Article",
                "operations": [
                    {"name": "get", "fields": field},
                    {"name": "list", "fields": field},
                ],
            }
        ],
    }
    inv_app = {
        "module": "inventory",
        "resources": [
            {
                "model": "inventory.models.Product",
                "operations": [
                    {"name": "get", "fields": field},
                    {"name": "list", "fields": field},
                ],
            }
        ],
    }
    project_cfg = _write_json_config(
        tmp_path,
        {
            "auth": {
                "type": "jwt",
                "credentials_schema": "myapp.auth.LoginCredentials",
                "validate_fn": "myapp.auth.validate",
                "get_session_fn": "myapp.auth.get_session",
            },
            "databases": [{"key": "primary", "default": True}],
            "apps": [
                {"config": blog_app, "prefix": "/blog"},
                {"config": inv_app, "prefix": "/inventory"},
            ],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["generate", "--config", str(project_cfg), "--out", str(out)],
    )
    assert result.exit_code == 0
    assert (out / "db" / "primary_session.py").exists()
    assert (out / "auth" / "router.py").exists()
    assert (out / "blog" / "routes" / "article.py").exists()
    assert (out / "inventory" / "routes" / "product.py").exists()
    assert (out / "routes" / "__init__.py").exists()
    root_router = (out / "routes" / "__init__.py").read_text()
    assert "blog_router" in root_router
    assert "inventory_router" in root_router


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


def test_clean_removes_out_dir(tmp_path: Path):
    cfg = _write_json_config(tmp_path, _project_with())
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.py").write_text("old")
    result = runner.invoke(
        app, ["clean", "--config", str(cfg), "--out", str(out)]
    )
    assert result.exit_code == 0
    assert "Cleaned" in result.output
    assert not out.exists()


def test_clean_noop_when_out_missing(tmp_path: Path):
    cfg = _write_json_config(tmp_path, _project_with())
    missing = tmp_path / "never_existed"
    result = runner.invoke(
        app, ["clean", "--config", str(cfg), "--out", str(missing)]
    )
    assert result.exit_code == 0
    assert "Nothing to clean" in result.output


def test_clean_bad_config_raises_config_error(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1")
    result = runner.invoke(
        app, ["clean", "--config", str(bad), "--out", str(tmp_path)]
    )
    assert isinstance(result.exception, ConfigError)


def test_generate_clean_flag_removes_stale(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        _project_with(
            resources=[
                {
                    "model": "myapp.models.Post",
                    "operations": [
                        {
                            "name": "get",
                            "fields": [{"name": "title", "type": "str"}],
                        },
                    ],
                }
            ],
        ),
    )
    out = tmp_path / "out"
    out.mkdir()
    stale = out / "stale.py"
    stale.write_text("old")
    result = runner.invoke(
        app,
        [
            "generate",
            "--config",
            str(cfg),
            "--out",
            str(out),
            "--clean",
        ],
    )
    assert result.exit_code == 0
    assert not stale.exists()
    assert (out / "myapp" / "routes" / "post.py").exists()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_accepts_good_config(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "databases": [{"key": "primary", "default": True}],
            "resources": [],
        },
    )
    result = runner.invoke(app, ["validate", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_validate_bad_config_raises_config_error(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1")
    result = runner.invoke(app, ["validate", "--config", str(bad)])
    assert isinstance(result.exception, ConfigError)


def test_validate_does_not_write_files(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        {
            "module": "myapp",
            "databases": [{"key": "primary", "default": True}],
            "resources": [
                {
                    "model": "myapp.models.Post",
                    "operations": [
                        {
                            "name": "get",
                            "fields": [{"name": "title", "type": "str"}],
                        },
                    ],
                }
            ],
        },
    )
    out = tmp_path / "out"
    result = runner.invoke(app, ["validate", "--config", str(cfg)])
    assert result.exit_code == 0
    assert not out.exists()


# ---------------------------------------------------------------------------
# targets list
# ---------------------------------------------------------------------------


def test_targets_list_shows_kiln():
    result = runner.invoke(app, ["targets", "list"])
    assert result.exit_code == 0
    assert "kiln" in result.output
    assert "python" in result.output


# ---------------------------------------------------------------------------
# generate --dry-run
# ---------------------------------------------------------------------------


def test_generate_dry_run_lists_files_without_writing(tmp_path: Path):
    cfg = _write_json_config(
        tmp_path,
        _project_with(
            resources=[
                {
                    "model": "myapp.models.Post",
                    "operations": [
                        {
                            "name": "get",
                            "fields": [{"name": "title", "type": "str"}],
                        },
                    ],
                }
            ],
        ),
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "generate",
            "--config",
            str(cfg),
            "--out",
            str(out),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Would generate" in result.output
    assert str(out / "myapp" / "routes" / "post.py") in result.output
    assert not out.exists()


def test_generate_dry_run_rejects_clean(tmp_path: Path):
    cfg = _write_json_config(tmp_path, _project_with())
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "generate",
            "--config",
            str(cfg),
            "--out",
            str(out),
            "--dry-run",
            "--clean",
        ],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)


# ---------------------------------------------------------------------------
# write_files
# ---------------------------------------------------------------------------


def testwrite_files_always_overwrites(tmp_path: Path):
    f = GeneratedFile(path="bar.py", content="# v1")
    write_files([f], tmp_path)
    f2 = GeneratedFile(path="bar.py", content="# v2")
    written = write_files([f2], tmp_path)
    assert written == 1
    assert (tmp_path / "bar.py").read_text() == "# v2"


def testwrite_files_creates_subdirs(tmp_path: Path):
    f = GeneratedFile(path="a/b/c.py", content="x")
    written = write_files([f], tmp_path)
    assert written == 1
    assert (tmp_path / "a" / "b" / "c.py").exists()
