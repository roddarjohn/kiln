"""Tests for the fe target.

The fe target is a thin wrapper over ``@hey-api/openapi-ts``:
its only job is to translate ``config/fe.jsonnet`` into an
``openapi-ts.config.ts`` file at the project root.  These
tests cover the schema, entry-point wiring, and the shape of
the emitted config file.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from be.target import target as be_target
from fe.config import ProjectConfig
from fe.operations import OpenApiTsConfig
from fe.target import target as fe_target
from foundry.config import load_config
from foundry.operation import load_registry
from foundry.pipeline import generate
from foundry.target import discover_targets

if TYPE_CHECKING:
    from pathlib import Path


# -------------------------------------------------------------------
# Config schema
# -------------------------------------------------------------------


def test_config_defaults():
    cfg = ProjectConfig()

    assert cfg.openapi_spec == "../be/openapi.json"
    assert cfg.output_dir == "src/_generated"
    assert cfg.client == "@hey-api/client-fetch"
    assert cfg.react_query is True
    assert cfg.format is None


def test_config_accepts_axios_client():
    cfg = ProjectConfig(client="@hey-api/client-axios")

    assert cfg.client == "@hey-api/client-axios"


def test_config_rejects_unknown_client():
    with pytest.raises(ValueError, match="Input should be"):
        ProjectConfig(client="@hey-api/client-bogus")  # type: ignore[arg-type]


def test_config_rejects_unknown_formatter():
    with pytest.raises(ValueError, match="Input should be"):
        ProjectConfig(format="rome")  # type: ignore[arg-type]


# -------------------------------------------------------------------
# Target wiring
# -------------------------------------------------------------------


def test_fe_target_is_registered_via_entry_point():
    names = {t.name for t in discover_targets()}

    assert "fe" in names


def test_fe_target_declares_its_own_entry_point_group():
    assert fe_target.operations_entry_point == "fe.operations"
    assert fe_target.operations_entry_point != be_target.operations_entry_point


def test_fe_entry_point_group_holds_expected_ops():
    registry = load_registry("fe.operations")
    names = {entry.meta.name for entry in registry.entries}

    assert names == {
        "openapi_ts_config",
        "scaffold",
        "auth",
        "resource_list",
        "resource_form",
        "resource_action",
    }


# -------------------------------------------------------------------
# Operation: emitted file paths
# -------------------------------------------------------------------


def _build_files(cfg: ProjectConfig) -> dict[str, str]:
    """Run the full pipeline and return ``{path: content}``."""
    return {f.path: f.content for f in generate(cfg, fe_target)}


def test_emits_openapi_ts_config():
    files = _build_files(ProjectConfig())

    # Scaffold always emits api/client.ts and App.tsx; Shell.tsx
    # is conditional on the shell config (absent here).
    assert "openapi-ts.config.ts" in files
    assert "src/api/client.ts" in files
    assert "src/App.tsx" in files
    assert "src/Shell.tsx" not in files


# -------------------------------------------------------------------
# Rendered content: openapi-ts.config.ts
# -------------------------------------------------------------------


def test_config_file_imports_define_config():
    content = _build_files(ProjectConfig())["openapi-ts.config.ts"]

    assert 'from "@hey-api/openapi-ts"' in content
    assert "import { defineConfig }" in content
    assert "export default defineConfig(" in content


def test_config_file_carries_input_path():
    content = _build_files(
        ProjectConfig(openapi_spec="../api/openapi.json"),
    )["openapi-ts.config.ts"]

    assert 'input: "../api/openapi.json"' in content


def test_config_file_carries_output_dir():
    content = _build_files(ProjectConfig(output_dir="src/api"))[
        "openapi-ts.config.ts"
    ]

    # ``path:`` because openapi-ts's output is an object with a
    # ``path`` field (and optional ``format``); a bare string
    # would parse but loses the future ability to set format /
    # caseInsensitive / etc. without a config rewrite.
    assert 'path: "src/api"' in content


def test_config_file_includes_client_plugin():
    content = _build_files(ProjectConfig(client="@hey-api/client-axios"))[
        "openapi-ts.config.ts"
    ]

    assert '"@hey-api/client-axios"' in content
    assert '"@hey-api/client-fetch"' not in content


def test_react_query_on_includes_tanstack_plugin():
    content = _build_files(ProjectConfig(react_query=True))[
        "openapi-ts.config.ts"
    ]

    assert '"@tanstack/react-query"' in content


def test_react_query_off_omits_tanstack_plugin():
    content = _build_files(ProjectConfig(react_query=False))[
        "openapi-ts.config.ts"
    ]

    assert "@tanstack/react-query" not in content


def test_typescript_and_sdk_plugins_always_present():
    # ``@hey-api/typescript`` and ``@hey-api/sdk`` are the bare
    # minimum: types and the typed SDK.  Without them openapi-ts
    # generates nothing useful, so they're not user-toggleable.
    content = _build_files(ProjectConfig())["openapi-ts.config.ts"]

    assert '"@hey-api/typescript"' in content
    assert '"@hey-api/sdk"' in content


def test_format_field_omitted_when_unset():
    content = _build_files(ProjectConfig())["openapi-ts.config.ts"]

    # Emitting ``format: null`` would make openapi-ts read the
    # field as ``null``, not "absent" -- different semantics.
    assert "format:" not in content


def test_format_field_emitted_when_set():
    content = _build_files(ProjectConfig(format="prettier"))[
        "openapi-ts.config.ts"
    ]

    assert 'format: "prettier"' in content


def test_emitted_config_is_overwrite():
    # ``openapi-ts.config.ts`` is a derived artefact -- the user's
    # source of truth is ``config/fe.jsonnet``.  Re-running fe
    # MUST overwrite the file unconditionally; if it were
    # ``"skip"`` a stale config would survive a kiln-config edit
    # and the user would chase ghosts.
    files = generate(ProjectConfig(), fe_target)

    cfg_file = next(f for f in files if f.path == "openapi-ts.config.ts")
    assert cfg_file.if_exists == "overwrite"


# -------------------------------------------------------------------
# Plugin order: client first, then react-query, then typescript+sdk
# -------------------------------------------------------------------


def test_plugin_order_is_stable():
    # openapi-ts walks plugins in declaration order, so a stable
    # order keeps generated output stable across re-runs (no
    # diff churn just because dict iteration changed).
    content = _build_files(ProjectConfig())["openapi-ts.config.ts"]
    plugins = re.findall(r'"@[^"]+"', content)

    # The "@hey-api/openapi-ts" import is also a quoted package
    # name -- skip it.
    plugins = [p for p in plugins if p != '"@hey-api/openapi-ts"']

    assert plugins == [
        '"@hey-api/client-fetch"',
        '"@tanstack/react-query"',
        '"@hey-api/typescript"',
        '"@hey-api/sdk"',
    ]


# -------------------------------------------------------------------
# Config loading round-trip
# -------------------------------------------------------------------


def test_load_config_accepts_minimal_jsonnet(tmp_path: Path):
    cfg_file = tmp_path / "fe.jsonnet"
    cfg_file.write_text('{ openapi_spec: "../be/openapi.json" }')

    cfg = load_config(cfg_file, ProjectConfig)

    assert isinstance(cfg, ProjectConfig)
    assert cfg.openapi_spec == "../be/openapi.json"


# -------------------------------------------------------------------
# Operation -- direct invocation (no engine)
# -------------------------------------------------------------------


def test_openapi_ts_config_yields_one_static_file():
    from foundry.engine import BuildContext
    from foundry.outputs import StaticFile
    from foundry.scope import PROJECT
    from foundry.store import BuildStore

    cfg = ProjectConfig()
    ctx = BuildContext(
        config=cfg,
        scope=PROJECT,
        instance=cfg,
        instance_id="project",
        store=BuildStore(scope_tree=()),
        package_prefix="",
    )

    items = list(OpenApiTsConfig().build(ctx, OpenApiTsConfig.Options()))

    assert len(items) == 1
    assert isinstance(items[0], StaticFile)
    assert items[0].path == "openapi-ts.config.ts"
