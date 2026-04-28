"""Tests for the kiln_root target.

Covers config defaults, the pyproject entry-point registration,
the static files emitted by :class:`~kiln_root.operations.RootScaffold`,
and registry isolation against the kiln target's ops.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from foundry.config import load_config
from foundry.pipeline import generate
from foundry.target import discover_targets
from kiln.target import target as kiln_target
from kiln_root.config import RootConfig
from kiln_root.operations import REGISTRY, RootScaffold
from kiln_root.target import target as root_target

if TYPE_CHECKING:
    from pathlib import Path


# -------------------------------------------------------------------
# Config schema
# -------------------------------------------------------------------


def test_root_config_defaults_render_a_usable_skeleton():
    cfg = RootConfig()

    assert cfg.name == "myapp"
    assert cfg.module == "app"
    assert "FastAPI" in cfg.description
    assert cfg.package_prefix == ""


def test_root_config_overrides_take_effect():
    cfg = RootConfig(
        name="demo-app",
        module="demo",
        description="Demo bootstrap.",
    )

    assert cfg.name == "demo-app"
    assert cfg.module == "demo"
    assert cfg.description == "Demo bootstrap."


# -------------------------------------------------------------------
# Target wiring
# -------------------------------------------------------------------


def test_root_target_is_registered_via_entry_point():
    names = {t.name for t in discover_targets()}

    assert "kiln_root" in names


def test_root_target_uses_isolated_registry():
    # The two targets ship in the same package; the registry on
    # each must point to a different object so kiln's resource-
    # scope ops never fire on a kiln_root config.
    assert root_target.registry is not kiln_target.registry
    assert root_target.registry is REGISTRY


def test_root_target_registry_holds_only_root_scaffold():
    names = {entry.meta.name for entry in REGISTRY.entries}

    assert names == {"root_scaffold"}


# -------------------------------------------------------------------
# RootScaffold operation -- emitted file paths
# -------------------------------------------------------------------


_EXPECTED_PATHS = {
    "main.py",
    "pyproject.toml",
    "justfile",
    ".gitignore",
    ".python-version",
    "config/project.jsonnet",
}


def _build_files(cfg: RootConfig) -> dict[str, str]:
    """Run the full pipeline and return ``{path: content}``."""
    return {f.path: f.content for f in generate(cfg, root_target)}


def test_root_scaffold_emits_expected_paths():
    files = _build_files(RootConfig(module="demo"))

    for expected in _EXPECTED_PATHS:
        assert expected in files

    assert "config/demo.jsonnet" in files
    assert "demo/__init__.py" in files


def test_root_scaffold_module_field_drives_paths():
    files = _build_files(RootConfig(module="tracker"))

    assert "config/tracker.jsonnet" in files
    assert "tracker/__init__.py" in files
    assert "config/app.jsonnet" not in files


# -------------------------------------------------------------------
# Rendered content
# -------------------------------------------------------------------


def test_main_py_imports_from_package_prefix():
    cfg = RootConfig(name="demo-app", description="Demo.")
    files = _build_files(cfg)

    main = files["main.py"]
    assert 'title="demo-app"' in main
    assert 'description="Demo."' in main
    assert "from _generated.routes import router" in main


def test_pyproject_carries_name_and_kiln_dep():
    cfg = RootConfig(name="demo-app", description="Demo.")
    files = _build_files(cfg)

    py = files["pyproject.toml"]
    assert 'name = "demo-app"' in py
    assert 'description = "Demo."' in py
    assert "kiln-generator" in py
    # The exclude line must reference the same package prefix the
    # justfile points kiln at, otherwise lint walks into generated
    # code on every commit.
    assert 'exclude = ["_generated"]' in py


def test_justfile_includes_openapi_recipe():
    files = _build_files(RootConfig())

    just = files["justfile"]
    assert "openapi:" in just
    assert "main.app.openapi()" in just
    assert "> openapi.json" in just


def test_justfile_preserves_just_variable_syntax():
    # ``{% raw %}`` blocks in the template are what keep these
    # interpolations from being eaten by Jinja.  Regression test
    # so a future template edit doesn't quietly break ``just generate``.
    files = _build_files(RootConfig())

    just = files["justfile"]
    assert "--config {{ config }}" in just
    assert "--out {{ out }}" in just
    assert "{{ out }}/tests/" in just


def test_project_jsonnet_points_at_app_module():
    cfg = RootConfig(name="demo-app", module="tracker")
    files = _build_files(cfg)

    project = files["config/project.jsonnet"]
    assert "demo-app" in project
    assert 'import "tracker.jsonnet"' in project
    assert 'prefix: "/tracker"' in project


def test_app_jsonnet_uses_module_name():
    files = _build_files(RootConfig(module="tracker"))

    app_cfg = files["config/tracker.jsonnet"]
    assert 'module: "tracker"' in app_cfg


def test_module_init_is_empty():
    files = _build_files(RootConfig(module="tracker"))

    assert files["tracker/__init__.py"] == ""


# -------------------------------------------------------------------
# Config loading round-trip via foundry.load_config
# -------------------------------------------------------------------


def test_load_config_accepts_minimal_json(tmp_path: Path):
    cfg_file = tmp_path / "root.json"
    cfg_file.write_text(json.dumps({"name": "demo", "module": "demo"}))

    cfg = load_config(cfg_file, RootConfig)

    assert isinstance(cfg, RootConfig)
    assert cfg.name == "demo"
    assert cfg.module == "demo"


def test_load_config_accepts_jsonnet(tmp_path: Path):
    cfg_file = tmp_path / "root.jsonnet"
    cfg_file.write_text('{ name: "demo", module: "demo" }')

    cfg = load_config(cfg_file, RootConfig)

    assert isinstance(cfg, RootConfig)
    assert cfg.name == "demo"


# -------------------------------------------------------------------
# Operation -- direct invocation (no engine)
# -------------------------------------------------------------------


def test_root_scaffold_yields_static_files():
    from foundry.engine import BuildContext
    from foundry.outputs import StaticFile
    from foundry.scope import PROJECT
    from foundry.store import BuildStore

    cfg = RootConfig(module="demo")
    ctx = BuildContext(
        config=cfg,
        scope=PROJECT,
        instance=cfg,
        instance_id="project",
        store=BuildStore(scope_tree=()),
        package_prefix="_generated",
    )

    items = list(RootScaffold().build(ctx, RootScaffold.Options()))

    assert all(isinstance(item, StaticFile) for item in items)
    paths = {item.path for item in items}
    assert "main.py" in paths
    assert "demo/__init__.py" in paths


# -------------------------------------------------------------------
# Smoke test against a real generated tree
# -------------------------------------------------------------------


def test_pipeline_writes_files_with_expected_paths():
    cfg = RootConfig(name="demo-app", module="demo")

    files = generate(cfg, root_target)
    paths = sorted(f.path for f in files)

    assert ".gitignore" in paths
    assert ".python-version" in paths
    assert "config/demo.jsonnet" in paths
    assert "demo/__init__.py" in paths


@pytest.mark.parametrize("module_name", ["demo", "tracker", "service_api"])
def test_pipeline_handles_various_module_names(module_name: str):
    cfg = RootConfig(module=module_name)

    files = {f.path for f in generate(cfg, root_target)}

    assert f"config/{module_name}.jsonnet" in files
    assert f"{module_name}/__init__.py" in files
