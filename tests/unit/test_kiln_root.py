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


def test_root_config_option_flags_default_off():
    # Defaults stay opinionated -- nothing extra is wired in
    # unless the user explicitly opts in.  Keeps the bare
    # bootstrap minimal.
    cfg = RootConfig()

    assert cfg.opentelemetry is False
    assert cfg.files is False
    assert cfg.psycopg is False
    assert cfg.pgcraft is False
    assert cfg.pgqueuer is False
    assert cfg.editable is False


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


# -------------------------------------------------------------------
# Always-on dependencies and ruff config
# -------------------------------------------------------------------


def test_pyproject_always_includes_pyjwt_and_multipart():
    # ``pyjwt`` and ``python-multipart`` are tiny but easy to
    # forget; shipping them by default avoids a confusing missing-
    # module the moment the user enables auth in project.jsonnet.
    py = _build_files(RootConfig())["pyproject.toml"]

    assert "pyjwt>=2.0" in py
    assert "python-multipart>=0.0.22" in py


def test_pyproject_always_includes_per_file_ignores():
    # The per-file ignores match kiln's idiomatic output --
    # without them ruff trips on standard SQLAlchemy table_args
    # patterns and on kiln action signatures.  Shipping them
    # default-on saves every consumer the same triage.
    py = _build_files(RootConfig())["pyproject.toml"]

    assert "[tool.ruff.lint.per-file-ignores]" in py
    assert '"**/models/**"' in py
    assert '"**/actions.py"' in py
    assert '"tests/**"' in py


# -------------------------------------------------------------------
# opentelemetry flag
# -------------------------------------------------------------------


def test_opentelemetry_off_omits_telemetry_init():
    files = _build_files(RootConfig())

    py = files["pyproject.toml"]
    main = files["main.py"]

    # Bare ``"kiln-generator"`` rather than the extras form.
    assert '"kiln-generator",' in py
    assert "opentelemetry" not in py.lower()
    assert "init_telemetry" not in main


def test_opentelemetry_on_emits_extras_and_init():
    files = _build_files(RootConfig(opentelemetry=True))

    py = files["pyproject.toml"]
    main = files["main.py"]

    assert '"kiln-generator[opentelemetry]"' in py
    assert "from _generated.telemetry import init_telemetry" in main
    assert "init_telemetry(app)" in main


# -------------------------------------------------------------------
# files flag
# -------------------------------------------------------------------


def test_files_on_adds_files_extra():
    py = _build_files(RootConfig(files=True))["pyproject.toml"]

    assert '"kiln-generator[files]"' in py


def test_opentelemetry_and_files_combine_into_one_extras_list():
    # Two extras must end up on a single ``kiln-generator[a,b]``
    # line -- pip / uv can't merge two separate kiln-generator
    # entries into a coherent install.
    py = _build_files(RootConfig(opentelemetry=True, files=True))[
        "pyproject.toml"
    ]

    assert '"kiln-generator[opentelemetry,files]"' in py
    # And only one kiln-generator line exists.
    assert py.count('"kiln-generator') == 1


# -------------------------------------------------------------------
# psycopg / pgcraft flags
# -------------------------------------------------------------------


def test_psycopg_flag_adds_dep():
    py_off = _build_files(RootConfig())["pyproject.toml"]
    py_on = _build_files(RootConfig(psycopg=True))["pyproject.toml"]

    assert "psycopg[binary]" not in py_off
    assert '"psycopg[binary]>=3.2"' in py_on


def test_pgcraft_flag_adds_dep():
    py_off = _build_files(RootConfig())["pyproject.toml"]
    py_on = _build_files(RootConfig(pgcraft=True))["pyproject.toml"]

    assert '"pgcraft"' not in py_off
    assert '"pgcraft"' in py_on


# -------------------------------------------------------------------
# editable flag
# -------------------------------------------------------------------


def test_editable_off_omits_uv_sources():
    py = _build_files(RootConfig())["pyproject.toml"]

    assert "[tool.uv.sources]" not in py


def test_editable_on_pins_kiln_to_local_path():
    py = _build_files(RootConfig(editable=True))["pyproject.toml"]

    assert "[tool.uv.sources]" in py
    assert 'kiln-generator = { path = "../kiln", editable = true }' in py


def test_editable_with_pgcraft_pins_pgcraft_too():
    py = _build_files(RootConfig(editable=True, pgcraft=True))["pyproject.toml"]

    assert 'pgcraft = { path = "../pgcraft", editable = true }' in py


def test_editable_without_pgcraft_omits_pgcraft_source():
    # Editable=True but pgcraft=False shouldn't half-pin pgcraft
    # at a path that may not exist on the user's machine.
    py = _build_files(RootConfig(editable=True))["pyproject.toml"]

    assert "pgcraft = { path" not in py


# -------------------------------------------------------------------
# pgqueuer flag (justfile)
# -------------------------------------------------------------------


def test_pgqueuer_off_omits_queue_recipes():
    just = _build_files(RootConfig())["justfile"]

    assert "queue-install:" not in just
    assert "worker:" not in just


def test_pgqueuer_on_emits_queue_recipes_using_module():
    just = _build_files(RootConfig(module="tracker", pgqueuer=True))["justfile"]

    assert "queue-install:" in just
    assert "worker:" in just
    # The worker recipe must point at the user's app module so
    # ``just worker`` actually runs the right pgqueuer entrypoint.
    assert "uv run pgq run tracker.queue.main:main" in just


def test_justfile_includes_openapi_recipe():
    files = _build_files(RootConfig())

    just = files["justfile"]
    assert "openapi:" in just
    assert "main.app.openapi()" in just
    assert "> openapi.json" in just


def test_justfile_includes_bootstrap_recipe():
    # The bootstrap recipe lets users re-run kiln_root after the
    # initial scaffold without having to remember the foundry CLI
    # incantation.  The destructive-warning comment is part of the
    # contract -- without it the recipe is too easy to fire by
    # mistake.
    files = _build_files(RootConfig())

    just = files["justfile"]
    assert "bootstrap:" in just
    assert "--target kiln_root" in just
    assert "{{ bootstrap }}" in just
    assert "post-bootstrap edits" in just


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


# Files where a comment header is meaningful (the format permits
# it and the file is non-empty).  ``.python-version`` is plaintext
# read line-by-line by pyenv/uv so it can't carry a comment;
# ``{module}/__init__.py`` is intentionally empty as the user's
# package marker, so we don't decorate it either.
_HEADER_PATHS = (
    "main.py",
    "pyproject.toml",
    "justfile",
    ".gitignore",
    "config/project.jsonnet",
    "config/tracker.jsonnet",
)


@pytest.mark.parametrize("path", _HEADER_PATHS)
def test_autogenerated_header_present_in(path: str):
    # Re-running ``just bootstrap`` clobbers these files; the
    # banner makes that lossy contract obvious to anyone reading
    # the files later.  Without it users habitually edit the
    # scaffold, regen, and lose changes silently.
    files = _build_files(RootConfig(name="demo-app", module="tracker"))

    content = files[path]
    assert "AUTOGENERATED by kiln_root" in content
    assert "bootstrap.jsonnet" in content


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


# -------------------------------------------------------------------
# Re-bootstrap safety -- if_exists / --force / --force-paths
# -------------------------------------------------------------------


def test_every_kiln_root_file_declares_skip():
    # The whole point of marking outputs ``"skip"`` is so a
    # re-bootstrap doesn't blow away post-bootstrap edits.  If a
    # template ever forgets the flag, this test catches it.
    files = generate(RootConfig(module="demo"), root_target)

    for f in files:
        assert f.if_exists == "skip", (
            f'{f.path!r} must be if_exists="skip" so re-running '
            f"kiln_root doesn't overwrite user edits"
        )


def _run_bootstrap(
    tmp_path: Path,
    *extra_args: str,
    config_text: str = '{ name: "demo", module: "demo" }',
) -> object:
    """Invoke the foundry CLI to bootstrap into *tmp_path*."""
    from typer.testing import CliRunner

    from foundry.cli import app

    cfg = tmp_path / "bootstrap.jsonnet"
    cfg.write_text(config_text)

    return CliRunner().invoke(
        app,
        [
            "generate",
            "--target",
            "kiln_root",
            "--config",
            str(cfg),
            "--out",
            str(tmp_path),
            *extra_args,
        ],
    )


def test_re_bootstrap_preserves_post_bootstrap_edits(tmp_path: Path):
    # Initial bootstrap.
    first = _run_bootstrap(tmp_path)
    assert first.exit_code == 0

    # User customizes a generated file.
    edited = '# user-edited pyproject\n[project]\nname = "renamed"\n'
    (tmp_path / "pyproject.toml").write_text(edited)

    # Re-bootstrap without --force: edits must survive.
    second = _run_bootstrap(tmp_path)
    assert second.exit_code == 0

    assert (tmp_path / "pyproject.toml").read_text() == edited
    assert "skipped" in second.output.lower()


def test_re_bootstrap_with_force_overwrites_everything(tmp_path: Path):
    _run_bootstrap(tmp_path)
    (tmp_path / "pyproject.toml").write_text("# user-edited")
    (tmp_path / "main.py").write_text("# user-edited")

    result = _run_bootstrap(tmp_path, "--force")
    assert result.exit_code == 0

    py = (tmp_path / "pyproject.toml").read_text()
    main = (tmp_path / "main.py").read_text()
    assert "AUTOGENERATED by kiln_root" in py
    assert "AUTOGENERATED by kiln_root" in main


def test_re_bootstrap_force_paths_targets_only_listed(tmp_path: Path):
    _run_bootstrap(tmp_path)
    (tmp_path / "pyproject.toml").write_text("# user-pyproject")
    (tmp_path / "main.py").write_text("# user-main")

    result = _run_bootstrap(tmp_path, "--force-paths", "pyproject.toml")
    assert result.exit_code == 0

    # pyproject got reset; main.py wasn't on the force list, so
    # the user's edit survives.
    assert (
        "AUTOGENERATED by kiln_root"
        in (tmp_path / "pyproject.toml").read_text()
    )
    assert (tmp_path / "main.py").read_text() == "# user-main"


def test_re_bootstrap_force_paths_accepts_comma_list(tmp_path: Path):
    _run_bootstrap(tmp_path)
    (tmp_path / "pyproject.toml").write_text("# user-pyproject")
    (tmp_path / "main.py").write_text("# user-main")
    (tmp_path / ".gitignore").write_text("# user-gitignore")

    result = _run_bootstrap(tmp_path, "--force-paths", "pyproject.toml,main.py")
    assert result.exit_code == 0

    assert (
        "AUTOGENERATED by kiln_root"
        in (tmp_path / "pyproject.toml").read_text()
    )
    assert "AUTOGENERATED by kiln_root" in (tmp_path / "main.py").read_text()
    # .gitignore wasn't on the list -- still user's content.
    assert (tmp_path / ".gitignore").read_text() == "# user-gitignore"


def test_re_bootstrap_force_paths_repeated_flag(tmp_path: Path):
    _run_bootstrap(tmp_path)
    (tmp_path / "pyproject.toml").write_text("# user-pyproject")
    (tmp_path / "main.py").write_text("# user-main")

    result = _run_bootstrap(
        tmp_path,
        "--force-paths",
        "pyproject.toml",
        "--force-paths",
        "main.py",
    )
    assert result.exit_code == 0

    assert (
        "AUTOGENERATED by kiln_root"
        in (tmp_path / "pyproject.toml").read_text()
    )
    assert "AUTOGENERATED by kiln_root" in (tmp_path / "main.py").read_text()
