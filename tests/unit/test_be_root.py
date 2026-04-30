"""Tests for the be_root target.

Covers config defaults, the pyproject entry-point registration,
the static files emitted by :class:`~be_root.operations.RootScaffold`,
and registry isolation against the be target's ops.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from be.target import target as be_target
from be_root.config import RootConfig
from be_root.operations import RootScaffold
from be_root.target import target as root_target
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
    assert cfg.auth is False
    assert cfg.psycopg is False
    assert cfg.pgcraft is False
    assert cfg.pgqueuer is False
    assert cfg.editable is False
    assert cfg.rate_limit is False
    assert cfg.comms is False
    assert cfg.notification_preferences is False


def test_root_config_comms_requires_pgqueuer():
    # The comms dispatch path is pgqueuer-backed; ``comms=True``
    # without ``pgqueuer=True`` would scaffold a worker that
    # can't run.  Reject the combination at config-load time.
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="comms=True requires pgqueuer"):
        RootConfig(comms=True)


def test_root_config_comms_with_pgqueuer_validates():
    cfg = RootConfig(comms=True, pgqueuer=True)
    assert cfg.comms is True
    assert cfg.pgqueuer is True


def test_root_config_notification_preferences_requires_comms():
    # The preference-resolver scaffold extends the comms platform;
    # without ``comms``, the resolver and resource would point at
    # nothing.  Reject at config-load time.
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="notification_preferences=True requires comms",
    ):
        RootConfig(notification_preferences=True)


def test_root_config_notification_preferences_with_comms_validates():
    cfg = RootConfig(
        notification_preferences=True,
        comms=True,
        pgqueuer=True,
    )
    assert cfg.notification_preferences is True


# -------------------------------------------------------------------
# Target wiring
# -------------------------------------------------------------------


def test_root_target_is_registered_via_entry_point():
    names = {t.name for t in discover_targets()}

    assert "be_root" in names


def test_root_target_declares_its_own_entry_point_group():
    # The two targets ship in the same package and overlap at the
    # ``project`` scope; foundry walks each target's
    # ``operations_entry_point`` separately so be's
    # resource-scope ops never fire on a be_root config.
    assert root_target.operations_entry_point == "be_root.operations"
    assert be_target.operations_entry_point == "be.operations"
    assert (
        root_target.operations_entry_point != be_target.operations_entry_point
    )


def test_root_entry_point_group_holds_only_root_scaffold():
    registry = load_registry("be_root.operations")
    names = {entry.meta.name for entry in registry.entries}

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


def test_pyproject_carries_name_and_kiln_generator_dep():
    cfg = RootConfig(name="demo-app", description="Demo.")
    files = _build_files(cfg)

    py = files["pyproject.toml"]
    assert 'name = "demo-app"' in py
    assert 'description = "Demo."' in py
    assert "kiln-generator" in py
    # The exclude line must reference the same package prefix the
    # justfile points be at, otherwise lint walks into generated
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
    # The per-file ignores match be's idiomatic output --
    # without them ruff trips on standard SQLAlchemy table_args
    # patterns and on be action signatures.  Shipping them
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


def test_opentelemetry_on_emits_telemetry_block_in_project_jsonnet():
    # ``init_telemetry(app)`` imports from ``_generated.telemetry``
    # which be only builds when ``telemetry: ...`` is set in the
    # be config.  Without this block the flag is a half-story
    # that ImportErrors at startup.
    project = _build_files(
        RootConfig(name="demo-app", opentelemetry=True),
    )["config/project.jsonnet"]

    assert (
        'local telemetry = import "be/telemetry/telemetry.libsonnet"' in project
    )
    assert 'telemetry: telemetry.otel("demo-app")' in project


def test_opentelemetry_off_omits_telemetry_block():
    # The banner mentions "telemetry" as a typical post-bootstrap
    # edit; only the block itself must be absent.
    project = _build_files(RootConfig())["config/project.jsonnet"]

    assert "telemetry.otel" not in project
    assert "be/telemetry/telemetry.libsonnet" not in project


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
# auth flag
# -------------------------------------------------------------------


def test_auth_off_omits_auth_py_and_auth_block():
    files = _build_files(RootConfig())

    assert "auth.py" not in files
    project = files["config/project.jsonnet"]
    assert "auth.jwt" not in project
    assert "be/auth/jwt.libsonnet" not in project


def test_auth_on_emits_auth_py_skeleton():
    files = _build_files(RootConfig(auth=True))

    assert "auth.py" in files
    auth_py = files["auth.py"]
    assert "class LoginCredentials" in auth_py
    assert "class Session" in auth_py
    assert "def validate_login" in auth_py
    # The stub raises so a forgotten swap-out fails loudly rather
    # than silently letting every login through.
    assert "NotImplementedError" in auth_py


def test_auth_on_emits_auth_block_in_project_jsonnet():
    # The auth block must point at ``auth.<symbol>`` (matching
    # the dotted paths the auth.py skeleton declares); otherwise
    # be's introspector will fail to resolve the references.
    project = _build_files(RootConfig(auth=True))["config/project.jsonnet"]

    assert 'local auth = import "be/auth/jwt.libsonnet"' in project
    assert 'credentials_schema: "auth.LoginCredentials"' in project
    assert 'session_schema: "auth.Session"' in project
    assert 'validate_fn: "auth.validate_login"' in project


def test_auth_py_is_skip_so_real_validate_login_survives_rebootstrap():
    # The auth flag would be a foot-gun if a re-bootstrap reset
    # the user's real credential check -- their authn would be
    # silently disabled.  Lock the policy in.
    cfg = RootConfig(auth=True)
    files = generate(cfg, root_target)

    auth_file = next(f for f in files if f.path == "auth.py")
    assert auth_file.if_exists == "skip"


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


def test_editable_on_pins_kiln_generator_to_local_path():
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


def test_pgcraft_off_omits_alembic_files():
    files = _build_files(RootConfig())

    assert "alembic.ini" not in files
    assert "migrations/env.py" not in files
    assert "migrations/script.py.mako" not in files


def test_pgcraft_on_emits_alembic_scaffold():
    files = _build_files(RootConfig(pgcraft=True, module="tracker"))

    assert "alembic.ini" in files
    assert "migrations/env.py" in files
    assert "migrations/script.py.mako" in files
    # versions/ is intentionally not scaffolded -- alembic creates it
    # on the first ``alembic revision`` run.
    assert "migrations/versions/.gitkeep" not in files


def test_pgcraft_alembic_env_imports_consumer_module():
    files = _build_files(RootConfig(pgcraft=True, module="tracker"))
    env = files["migrations/env.py"]

    assert "import tracker.models" in env
    assert "tracker.models.__path__" in env
    assert "from db.base import Base" in env
    # The pgcraft hooks need to be installed before the model
    # imports trigger pgcraft factories.
    assert "pgcraft_alembic_hook()" in env


def test_pgcraft_alembic_ini_points_at_migrations_dir():
    files = _build_files(RootConfig(pgcraft=True))
    ini = files["alembic.ini"]

    assert "script_location = %(here)s/migrations" in ini
    assert "[post_write_hooks]" in ini


def test_pgcraft_on_adds_alembic_dep_to_pyproject():
    files = _build_files(RootConfig(pgcraft=True))
    pyproject = files["pyproject.toml"]

    assert '"alembic>=' in pyproject


def test_pgcraft_off_omits_alembic_dep_from_pyproject():
    files = _build_files(RootConfig())
    pyproject = files["pyproject.toml"]

    assert '"alembic' not in pyproject


def test_pgcraft_on_emits_migrate_recipes_in_justfile():
    files = _build_files(RootConfig(pgcraft=True))
    just = files["justfile"]

    assert "migrate MSG=" in just
    assert "alembic revision --autogenerate" in just
    assert "db-upgrade:" in just
    assert "alembic upgrade head" in just
    # MSG variable must use raw braces so just sees `{{ MSG }}`.
    assert '"{{ MSG }}"' in just


def test_pgcraft_off_omits_migrate_recipes_from_justfile():
    files = _build_files(RootConfig())
    just = files["justfile"]

    assert "migrate MSG" not in just
    assert "db-upgrade:" not in just
    assert "alembic" not in just


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


# -------------------------------------------------------------------
# rate_limit flag
# -------------------------------------------------------------------


def test_rate_limit_off_omits_extra_and_block():
    files = _build_files(RootConfig())

    py = files["pyproject.toml"]
    project = files["config/project.jsonnet"]

    assert "rate-limit" not in py
    assert "rate_limit.slowapi" not in project
    assert "be/rate_limit/rate_limit.libsonnet" not in project


def test_rate_limit_on_adds_extra():
    py = _build_files(RootConfig(rate_limit=True))["pyproject.toml"]

    assert '"kiln-generator[rate-limit]"' in py


def test_rate_limit_on_emits_block_in_project_jsonnet():
    project = _build_files(
        RootConfig(rate_limit=True, module="tracker"),
    )["config/project.jsonnet"]

    assert (
        'local rate_limit = import "be/rate_limit/rate_limit.libsonnet"'
        in project
    )
    # The dotted path follows the user's module so the bootstrap
    # is a coherent starting point even before any models exist.
    assert (
        'rate_limit: rate_limit.slowapi("tracker.models.RateLimitBucket")'
        in project
    )


def test_rate_limit_combines_with_other_extras():
    # Multiple extras must end up on a single ``kiln-generator[a,b,c]``
    # line -- pip / uv can't merge separate kiln-generator entries.
    py = _build_files(
        RootConfig(opentelemetry=True, files=True, rate_limit=True),
    )["pyproject.toml"]

    assert '"kiln-generator[opentelemetry,files,rate-limit]"' in py
    assert py.count('"kiln-generator') == 1


# -------------------------------------------------------------------
# comms flag
# -------------------------------------------------------------------


def test_comms_off_omits_comms_py_and_block():
    files = _build_files(RootConfig())

    assert "comms.py" not in files
    project = files["config/project.jsonnet"]
    assert "comms.platform" not in project
    assert "be/comms/comms.libsonnet" not in project


def test_comms_on_emits_comms_py_skeleton():
    files = _build_files(RootConfig(comms=True, pgqueuer=True))

    assert "comms.py" in files
    comms_py = files["comms.py"]
    assert "class WelcomeContext" in comms_py
    # Stub raises so a forgotten swap-out fails loudly.
    assert "NotImplementedError" in comms_py
    # The dotted symbols the project.jsonnet block points at:
    assert "email_transport = " in comms_py
    assert "resolver = " in comms_py


def test_comms_on_emits_comms_block_in_project_jsonnet():
    # The block must point at ``comms.<symbol>`` (matching the
    # dotted paths the comms.py skeleton declares); otherwise be's
    # introspector fails to resolve them.
    project = _build_files(
        RootConfig(comms=True, pgqueuer=True, module="tracker"),
    )["config/project.jsonnet"]

    assert 'local comms = import "be/comms/comms.libsonnet"' in project
    assert 'message_model: "tracker.models.CommMessage"' in project
    assert 'recipient_model: "tracker.models.CommRecipient"' in project
    assert 'email: "comms.email_transport"' in project
    assert 'preferences: "comms.resolver"' in project
    assert 'context_schema: "comms.WelcomeContext"' in project
    # Jinja-escape: the rendered jsonnet must contain literal {{ name }}
    # for the comm-type's templates (consumed at runtime by
    # ingot.comms.JinjaRenderer, not at codegen time).
    assert "{{ name }}" in project


def test_comms_py_is_skip_so_real_transports_survive_rebootstrap():
    # The same foot-gun as auth.py -- a re-bootstrap that resets
    # the user's real transport implementation would silently
    # break delivery.  Lock the policy in.
    cfg = RootConfig(comms=True, pgqueuer=True)
    files = generate(cfg, root_target)

    comms_file = next(f for f in files if f.path == "comms.py")
    assert comms_file.if_exists == "skip"


def test_comms_on_keeps_pgqueuer_recipes():
    # The validator forces pgqueuer=True alongside comms=True; the
    # justfile must therefore emit the queue-install / worker
    # recipes the comms worker depends on.
    just = _build_files(
        RootConfig(comms=True, pgqueuer=True, module="tracker"),
    )["justfile"]

    assert "queue-install:" in just
    assert "worker:" in just


# -------------------------------------------------------------------
# notification_preferences flag
# -------------------------------------------------------------------


def test_notification_preferences_off_emits_stub_resolver():
    # Default comms scaffold: stub resolver that opts everyone in.
    files = _build_files(RootConfig(comms=True, pgqueuer=True))

    comms_py = files["comms.py"]
    assert "_StubPreferenceResolver" in comms_py
    assert "DbPreferenceResolver" not in comms_py
    # The stub doesn't query a model; no model import or
    # session-factory import sneaks in.  (The ``Mixin`` reference
    # in the file's docstring is the design-doc guidance; it's
    # always present.)
    assert "from app.models import NotificationPreference" not in comms_py
    assert "_session_factory" not in comms_py
    # And the per-app jsonnet doesn't get a preferences resource.
    app_jsonnet = files["config/app.jsonnet"]
    assert "notification-preferences" not in app_jsonnet


def test_notification_preferences_on_emits_real_resolver():
    files = _build_files(
        RootConfig(
            comms=True,
            pgqueuer=True,
            notification_preferences=True,
            module="tracker",
        ),
    )

    comms_py = files["comms.py"]
    # Real resolver replaces the stub.
    assert "DbPreferenceResolver" in comms_py
    assert "_StubPreferenceResolver" not in comms_py
    # Imports the consumer's NotificationPreference model from the
    # user's models module.
    assert "from tracker.models import NotificationPreference" in comms_py
    # And the sessionmaker from the generated tree.
    assert "_session_factory" in comms_py
    assert "from _generated.db.primary_session" in comms_py
    # The query uses the three columns the mixin guarantees.
    assert "NotificationPreference.subject_key" in comms_py
    assert "NotificationPreference.comm_type" in comms_py
    assert "NotificationPreference.method" in comms_py
    # ``resolver`` symbol is still exposed -- the project.jsonnet
    # block points at ``comms.resolver`` regardless of which
    # implementation is in place.
    assert "resolver = DbPreferenceResolver()" in comms_py


def test_notification_preferences_on_emits_resource_jsonnet_file():
    files = _build_files(
        RootConfig(
            comms=True,
            pgqueuer=True,
            notification_preferences=True,
            module="tracker",
        ),
    )

    # Resource lives in its own file under config/resources/ so it
    # composes with both inline-resource and import-resource shapes
    # of the per-app jsonnet.
    assert "config/resources/notification_preference.jsonnet" in files
    resource = files["config/resources/notification_preference.jsonnet"]

    assert 'model: "tracker.models.NotificationPreference"' in resource
    assert 'route_prefix: "/notification-preferences"' in resource
    assert "require_auth: true" in resource

    # All five CRUD ops emitted.
    assert 'name: "get"' in resource
    assert 'name: "list"' in resource
    assert 'name: "create"' in resource
    assert 'name: "update"' in resource
    assert 'name: "delete"' in resource

    # The four mixin columns are dumped on get/list and writeable
    # on create.  Update only mutates ``enabled`` -- the natural-
    # key triple identifies the row.
    assert 'name: "subject_key"' in resource
    assert 'name: "comm_type"' in resource
    assert 'name: "method"' in resource
    assert 'name: "enabled"' in resource


def test_notification_preferences_on_app_jsonnet_imports_the_resource():
    app_jsonnet = _build_files(
        RootConfig(
            comms=True,
            pgqueuer=True,
            notification_preferences=True,
            module="tracker",
        ),
    )["config/tracker.jsonnet"]

    # The per-app jsonnet imports the resource file rather than
    # inlining the (verbose) operation list -- keeps the file
    # short and matches the real-app pattern of one resource per
    # ``config/resources/`` file.
    assert 'import "resources/notification_preference.jsonnet"' in app_jsonnet


def test_notification_preferences_on_emits_model_file():
    files = _build_files(
        RootConfig(
            comms=True,
            pgqueuer=True,
            notification_preferences=True,
            module="tracker",
        ),
    )

    # Model is emitted under the user's app package so it's
    # discoverable alongside their other models.
    assert "tracker/models/notification_preference.py" in files
    model = files["tracker/models/notification_preference.py"]

    assert "class NotificationPreference" in model
    assert "from ingot.comms import NotificationPreferenceMixin" in model
    # ``Base`` is imported from ``db.base`` to match the
    # pgcraft scaffold's ``migrations/env.py`` (the comment in the
    # file points non-pgcraft users at where to adjust).
    assert "from db.base import Base" in model
    # Unique constraint on the natural-key triple.
    assert "UniqueConstraint" in model
    assert '"subject_key"' in model
    assert '"comm_type"' in model
    assert '"method"' in model


def test_notification_preferences_off_emits_no_extra_files():
    files = _build_files(RootConfig())

    assert "config/resources/notification_preference.jsonnet" not in files
    assert "app/models/notification_preference.py" not in files
    app_jsonnet = files["config/app.jsonnet"]
    assert "NotificationPreference" not in app_jsonnet
    assert "notification-preferences" not in app_jsonnet
    assert "resources/notification_preference.jsonnet" not in app_jsonnet


def test_notification_preferences_on_keeps_comms_block_pointing_at_resolver():
    # Sanity: the project.jsonnet ``comms`` block still references
    # "comms.resolver" -- the dotted path doesn't change between
    # stub and real, so the wiring stays the same end-to-end.
    project = _build_files(
        RootConfig(
            comms=True,
            pgqueuer=True,
            notification_preferences=True,
            module="tracker",
        ),
    )["config/project.jsonnet"]

    assert 'preferences: "comms.resolver"' in project


def test_justfile_includes_openapi_recipe():
    files = _build_files(RootConfig())

    just = files["justfile"]
    assert "openapi:" in just
    assert "main.app.openapi()" in just
    assert "> openapi.json" in just


def test_justfile_includes_bootstrap_recipe():
    # The bootstrap recipe lets users re-run be_root after the
    # initial scaffold without having to remember the foundry CLI
    # incantation.  The destructive-warning comment is part of the
    # contract -- without it the recipe is too easy to fire by
    # mistake.
    files = _build_files(RootConfig())

    just = files["justfile"]
    assert "bootstrap:" in just
    assert "--target be_root" in just
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
    assert "AUTOGENERATED by be_root" in content
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


def test_every_be_root_file_declares_skip():
    # The whole point of marking outputs ``"skip"`` is so a
    # re-bootstrap doesn't blow away post-bootstrap edits.  If a
    # template ever forgets the flag, this test catches it.
    files = generate(RootConfig(module="demo"), root_target)

    for f in files:
        assert f.if_exists == "skip", (
            f'{f.path!r} must be if_exists="skip" so re-running '
            f"be_root doesn't overwrite user edits"
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
            "be_root",
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
    assert "AUTOGENERATED by be_root" in py
    assert "AUTOGENERATED by be_root" in main


def test_re_bootstrap_force_paths_targets_only_listed(tmp_path: Path):
    _run_bootstrap(tmp_path)
    (tmp_path / "pyproject.toml").write_text("# user-pyproject")
    (tmp_path / "main.py").write_text("# user-main")

    result = _run_bootstrap(tmp_path, "--force-paths", "pyproject.toml")
    assert result.exit_code == 0

    # pyproject got reset; main.py wasn't on the force list, so
    # the user's edit survives.
    assert (
        "AUTOGENERATED by be_root" in (tmp_path / "pyproject.toml").read_text()
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
        "AUTOGENERATED by be_root" in (tmp_path / "pyproject.toml").read_text()
    )
    assert "AUTOGENERATED by be_root" in (tmp_path / "main.py").read_text()
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
        "AUTOGENERATED by be_root" in (tmp_path / "pyproject.toml").read_text()
    )
    assert "AUTOGENERATED by be_root" in (tmp_path / "main.py").read_text()
