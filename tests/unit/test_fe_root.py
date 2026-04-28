"""Tests for the fe_root target.

Covers config defaults, the pyproject entry-point registration,
the static files emitted by :class:`~fe_root.operations.RootScaffold`,
and registry isolation against the be / fe target ops.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from be.target import target as be_target
from fe.target import target as fe_target
from fe_root.config import RootConfig
from fe_root.operations import RootScaffold
from fe_root.target import target as root_target
from foundry.config import load_config
from foundry.operation import load_registry
from foundry.pipeline import generate
from foundry.target import discover_targets

if TYPE_CHECKING:
    from pathlib import Path


# -------------------------------------------------------------------
# Config schema
# -------------------------------------------------------------------


def test_root_config_defaults_render_a_usable_skeleton():
    cfg = RootConfig()

    assert cfg.name == "myapp"
    assert "Frontend" in cfg.description
    assert cfg.glaze is True
    assert cfg.editable is False
    assert cfg.openapi_spec == "../be/openapi.json"


def test_root_config_overrides_take_effect():
    cfg = RootConfig(
        name="demo-fe",
        description="Demo bootstrap.",
        glaze=False,
        editable=True,
        openapi_spec="../api/spec.json",
    )

    assert cfg.name == "demo-fe"
    assert cfg.description == "Demo bootstrap."
    assert cfg.glaze is False
    assert cfg.editable is True
    assert cfg.openapi_spec == "../api/spec.json"


# -------------------------------------------------------------------
# Target wiring
# -------------------------------------------------------------------


def test_fe_root_target_is_registered_via_entry_point():
    names = {t.name for t in discover_targets()}

    assert "fe_root" in names


def test_fe_root_target_declares_its_own_entry_point_group():
    # The four targets ship in the same package and overlap at the
    # ``project`` scope; foundry walks each target's
    # ``operations_entry_point`` separately so be / fe ops never
    # fire on a fe_root config.
    assert root_target.operations_entry_point == "fe_root.operations"
    assert (
        root_target.operations_entry_point != fe_target.operations_entry_point
    )
    assert (
        root_target.operations_entry_point != be_target.operations_entry_point
    )


def test_fe_root_entry_point_group_holds_only_root_scaffold():
    registry = load_registry("fe_root.operations")
    names = {entry.meta.name for entry in registry.entries}

    assert names == {"root_scaffold"}


# -------------------------------------------------------------------
# RootScaffold operation -- emitted file paths
# -------------------------------------------------------------------


_EXPECTED_PATHS = {
    "package.json",
    "justfile",
    "tsconfig.json",
    "vite.config.ts",
    "index.html",
    "src/main.tsx",
    "src/App.tsx",
    "src/index.css",
    ".gitignore",
    ".nvmrc",
    "config/fe.jsonnet",
}


def _build_files(cfg: RootConfig) -> dict[str, str]:
    """Run the full pipeline and return ``{path: content}``."""
    return {f.path: f.content for f in generate(cfg, root_target)}


def test_fe_root_scaffold_emits_expected_paths():
    files = _build_files(RootConfig())

    for expected in _EXPECTED_PATHS:
        assert expected in files


# -------------------------------------------------------------------
# Rendered content: package.json
# -------------------------------------------------------------------


def test_package_json_carries_name_and_description():
    files = _build_files(RootConfig(name="demo-fe", description="A demo."))

    pkg = json.loads(files["package.json"])
    assert pkg["name"] == "demo-fe"
    assert pkg["description"] == "A demo."


def test_package_json_includes_runtime_deps():
    pkg = json.loads(_build_files(RootConfig())["package.json"])

    deps = pkg["dependencies"]
    assert "@tanstack/react-query" in deps
    assert "react" in deps
    assert "react-dom" in deps
    assert "@hey-api/client-fetch" in deps


def test_package_json_includes_openapi_ts_devdep():
    pkg = json.loads(_build_files(RootConfig())["package.json"])

    assert "@hey-api/openapi-ts" in pkg["devDependencies"]


def test_glaze_on_pulls_in_glaze_dep():
    pkg = json.loads(_build_files(RootConfig(glaze=True))["package.json"])

    assert "@roddarjohn/glaze" in pkg["dependencies"]


def test_glaze_off_omits_glaze_dep():
    pkg = json.loads(_build_files(RootConfig(glaze=False))["package.json"])

    assert "@roddarjohn/glaze" not in pkg["dependencies"]


def test_editable_with_glaze_pins_local_path():
    pkg = json.loads(
        _build_files(RootConfig(glaze=True, editable=True))["package.json"],
    )

    # ``file:`` specifier resolves via npm/yarn to a sibling repo
    # checkout; right for development against unreleased glaze.
    assert pkg["dependencies"]["@roddarjohn/glaze"] == "file:../glaze"


def test_editable_without_glaze_does_not_inject_glaze():
    pkg = json.loads(
        _build_files(RootConfig(glaze=False, editable=True))["package.json"],
    )

    assert "@roddarjohn/glaze" not in pkg["dependencies"]


# -------------------------------------------------------------------
# Rendered content: index.css imports glaze styles
# -------------------------------------------------------------------


def test_index_css_imports_glaze_styles_when_glaze_on():
    css = _build_files(RootConfig(glaze=True))["src/index.css"]

    assert '@import "@roddarjohn/glaze/styles.css"' in css


def test_index_css_omits_glaze_import_when_glaze_off():
    # Without the import, Vite shouldn't try to resolve glaze --
    # which it can't, since the dep isn't installed.  The whole
    # point of the toggle is to keep the bare scaffold buildable
    # without the design-system dependency.
    css = _build_files(RootConfig(glaze=False))["src/index.css"]

    assert "glaze" not in css


# -------------------------------------------------------------------
# Rendered content: justfile
# -------------------------------------------------------------------


def test_justfile_includes_bootstrap_recipe():
    just = _build_files(RootConfig())["justfile"]

    assert "bootstrap:" in just
    assert "--target fe_root" in just


def test_justfile_chains_generate_through_openapi_ts():
    # ``just generate`` must regenerate ``openapi-ts.config.ts`` AND
    # run the actual openapi-ts codegen -- splitting the two would
    # leave the user editing config/fe.jsonnet and seeing no TS
    # change until they remembered to invoke openapi-ts manually.
    just = _build_files(RootConfig())["justfile"]

    assert "generate: openapi-ts-config" in just
    assert "yarn openapi-ts" in just


def test_justfile_openapi_ts_config_recipe_invokes_fe_target():
    just = _build_files(RootConfig())["justfile"]

    assert "openapi-ts-config:" in just
    assert "--target fe" in just


# -------------------------------------------------------------------
# Rendered content: starter config/fe.jsonnet
# -------------------------------------------------------------------


def test_fe_jsonnet_starter_uses_openapi_spec_value():
    cfg_text = _build_files(RootConfig(openapi_spec="../api/spec.json"))[
        "config/fe.jsonnet"
    ]

    assert 'openapi_spec: "../api/spec.json"' in cfg_text


def test_fe_jsonnet_starter_defaults_to_be_openapi_path():
    cfg_text = _build_files(RootConfig())["config/fe.jsonnet"]

    # Default points at the ``be/`` sibling produced by ``be``;
    # matches the standard monorepo layout the docs assume.
    assert 'openapi_spec: "../be/openapi.json"' in cfg_text


# -------------------------------------------------------------------
# Re-bootstrap safety
# -------------------------------------------------------------------


def test_every_fe_root_file_declares_skip():
    files = generate(RootConfig(), root_target)

    for f in files:
        assert f.if_exists == "skip", (
            f'{f.path!r} must be if_exists="skip" so re-running '
            f"fe_root doesn't overwrite user edits"
        )


# -------------------------------------------------------------------
# Config loading round-trip
# -------------------------------------------------------------------


def test_load_config_accepts_minimal_jsonnet(tmp_path: Path):
    cfg_file = tmp_path / "bootstrap.jsonnet"
    cfg_file.write_text('{ name: "demo" }')

    cfg = load_config(cfg_file, RootConfig)

    assert isinstance(cfg, RootConfig)
    assert cfg.name == "demo"


# -------------------------------------------------------------------
# Operation -- direct invocation (no engine)
# -------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(_EXPECTED_PATHS))
def test_root_scaffold_yields_each_static_file(path: str):
    from foundry.engine import BuildContext
    from foundry.outputs import StaticFile
    from foundry.scope import PROJECT
    from foundry.store import BuildStore

    cfg = RootConfig()
    ctx = BuildContext(
        config=cfg,
        scope=PROJECT,
        instance=cfg,
        instance_id="project",
        store=BuildStore(scope_tree=()),
        package_prefix="",
    )

    items = list(RootScaffold().build(ctx, RootScaffold.Options()))

    assert all(isinstance(item, StaticFile) for item in items)
    paths = {item.path for item in items}
    assert path in paths
