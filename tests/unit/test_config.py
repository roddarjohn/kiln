"""Tests for kiln config loading and schema validation."""

import json
from pathlib import Path

import pytest

from foundry.config import load_config
from foundry.errors import ConfigError
from kiln.config.schema import (
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    FieldSpec,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_project_config_defaults():
    cfg = ProjectConfig(databases=[DatabaseConfig(key="primary", default=True)])
    assert cfg.version == "1"
    assert cfg.auth is None
    assert cfg.apps == []


def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.module == "app"
    assert cfg.resources == []


def test_project_config_apps_mode_untouched():
    cfg = ProjectConfig.model_validate(
        {
            "databases": [{"key": "primary", "default": True}],
            "apps": [
                {"config": {"module": "blog"}, "prefix": "/blog"},
            ],
        }
    )
    assert len(cfg.apps) == 1
    assert cfg.apps[0].prefix == "/blog"
    assert cfg.apps[0].config.module == "blog"


def test_auth_config_defaults():
    auth = AuthConfig(
        credentials_schema="myapp.auth.LoginCredentials",
        session_schema="myapp.auth.Session",
        validate_fn="myapp.auth.validate",
    )
    assert auth.sources == ["bearer"]
    assert auth.secret_env == "JWT_SECRET"  # noqa: S105
    assert auth.algorithm == "HS256"
    assert auth.token_url == "/auth/token"  # noqa: S105


def test_auth_config_fields_required():
    fields = ("credentials_schema", "session_schema", "validate_fn")
    for missing in fields:
        kwargs = {
            "credentials_schema": "myapp.auth.LoginCredentials",
            "session_schema": "myapp.auth.Session",
            "validate_fn": "myapp.auth.validate",
        }
        del kwargs[missing]
        with pytest.raises(ValueError, match=missing):
            AuthConfig(**kwargs)


def test_auth_config_empty_sources_rejected():
    with pytest.raises(ValueError, match="at least 1"):
        AuthConfig(
            credentials_schema="myapp.auth.LoginCredentials",
            session_schema="myapp.auth.Session",
            validate_fn="myapp.auth.validate",
            sources=[],
        )


def test_auth_config_duplicate_sources_rejected():
    with pytest.raises(ValueError, match="duplicates"):
        AuthConfig(
            credentials_schema="myapp.auth.LoginCredentials",
            session_schema="myapp.auth.Session",
            validate_fn="myapp.auth.validate",
            sources=["bearer", "bearer"],
        )


def test_database_config_session_names():
    db = DatabaseConfig(key="primary")
    assert db.session_module == "db.primary_session"
    assert db.get_db_fn == "get_primary_db"


def test_project_config_resolve_database_by_default():
    cfg = ProjectConfig(
        databases=[
            DatabaseConfig(key="primary", default=True),
            DatabaseConfig(key="reports", default=False),
        ]
    )
    assert cfg.resolve_database(None).key == "primary"


def test_project_config_resolve_database_by_key():
    cfg = ProjectConfig(
        databases=[
            DatabaseConfig(key="primary", default=True),
            DatabaseConfig(key="reports", default=False),
        ]
    )
    assert cfg.resolve_database("reports").key == "reports"


def test_project_config_resolve_database_no_default_raises():
    cfg = ProjectConfig(databases=[DatabaseConfig(key="primary")])
    with pytest.raises(ValueError, match="default=True"):
        cfg.resolve_database(None)


def test_project_config_resolve_database_unknown_key_raises():
    cfg = ProjectConfig(databases=[DatabaseConfig(key="primary", default=True)])
    with pytest.raises(ValueError, match="No database with key"):
        cfg.resolve_database("missing")


def test_resource_config_defaults():
    r = ResourceConfig(model="myapp.models.User")
    assert r.pk == "id"
    assert r.pk_type == "uuid"
    assert r.route_prefix is None
    assert r.db_key is None
    assert r.require_auth is True
    assert r.operations == []


def test_resource_config_with_operation_configs():
    r = ResourceConfig(
        model="myapp.models.User",
        operations=[
            {"name": "get"},
            {
                "name": "list",
                "fields": [
                    {"name": "id", "type": "uuid"},
                    {"name": "email", "type": "email"},
                ],
            },
        ],
    )
    assert len(r.operations) == 2
    assert r.operations[0].name == "get"
    assert isinstance(r.operations[1], OperationConfig)
    assert r.operations[1].name == "list"
    assert r.operations[1].options["fields"][0]["name"] == "id"


def test_resource_config_require_auth_bool():
    r = ResourceConfig(model="myapp.models.User", require_auth=False)
    assert r.require_auth is False


def test_resource_config_custom_route_prefix():
    r = ResourceConfig(model="myapp.models.User", route_prefix="/people")
    assert r.route_prefix == "/people"


def test_resource_config_int_pk():
    r = ResourceConfig(model="myapp.models.Tag", pk="id", pk_type="int")
    assert r.pk_type == "int"


def test_operation_config_basic():
    oc = OperationConfig(name="get")
    assert oc.name == "get"
    assert oc.require_auth is None
    assert oc.options == {}


def test_operation_config_with_extras():
    oc = OperationConfig(
        name="create",
        fields=[{"name": "title", "type": "str"}],
    )
    assert oc.name == "create"
    assert oc.options == {"fields": [{"name": "title", "type": "str"}]}


def test_operation_config_require_auth_override():
    oc = OperationConfig(name="delete", require_auth=True)
    assert oc.require_auth is True
    assert oc.options == {}


def test_operation_config_action():
    oc = OperationConfig(
        name="publish",
        type="action",
        fn="blog.actions.publish",
        params=[{"name": "notify", "type": "bool"}],
    )
    assert oc.type == "action"
    assert oc.options["fn"] == "blog.actions.publish"
    assert oc.options["params"][0]["name"] == "notify"


def test_operation_config_options_excludes_known_fields():
    oc = OperationConfig(
        name="create",
        require_auth=True,
        fields=[{"name": "x", "type": "str"}],
    )
    assert "name" not in oc.options
    assert "require_auth" not in oc.options
    assert "fields" in oc.options


def test_field_spec():
    f = FieldSpec(name="title", type="str")
    assert f.name == "title"
    assert f.type == "str"
    assert f.is_nested is False


def test_field_spec_nested_requires_model_and_fields():
    f = FieldSpec(
        name="project",
        type="nested",
        model="blog.models.Project",
        fields=[FieldSpec(name="id", type="uuid")],
    )
    assert f.is_nested is True
    assert f.model == "blog.models.Project"
    assert f.fields is not None
    assert f.many is False


def test_field_spec_nested_without_model_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="require `model` and `fields`"):
        FieldSpec(
            name="project",
            type="nested",
            fields=[FieldSpec(name="id", type="uuid")],
        )


def test_field_spec_nested_without_fields_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="require `model` and `fields`"):
        FieldSpec(
            name="project",
            type="nested",
            model="blog.models.Project",
        )


def test_field_spec_nested_empty_fields_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="must be non-empty"):
        FieldSpec(
            name="project",
            type="nested",
            model="blog.models.Project",
            fields=[],
        )


def test_field_spec_scalar_with_model_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="only allowed when"):
        FieldSpec(name="project", type="str", model="blog.models.Project")


def test_field_spec_many_on_scalar_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="only meaningful when"):
        FieldSpec(name="project", type="str", many=True)


def test_field_spec_nested_many_allowed():
    f = FieldSpec(
        name="articles",
        type="nested",
        model="blog.models.Article",
        fields=[FieldSpec(name="id", type="uuid")],
        many=True,
    )
    assert f.many is True


def test_field_spec_nested_load_defaults_to_selectin():
    f = FieldSpec(
        name="project",
        type="nested",
        model="blog.models.Project",
        fields=[FieldSpec(name="id", type="uuid")],
    )
    assert f.load == "selectin"


def test_field_spec_nested_load_override():
    f = FieldSpec(
        name="project",
        type="nested",
        model="blog.models.Project",
        fields=[FieldSpec(name="id", type="uuid")],
        load="joined",
    )
    assert f.load == "joined"


def test_field_spec_load_on_scalar_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="`load` is only meaningful"):
        FieldSpec(name="title", type="str", load="joined")


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


def _load(path: Path) -> ProjectConfig:
    from kiln.target import target as kiln_target

    stdlibs = (
        {kiln_target.name: kiln_target.jsonnet_stdlib_dir}
        if kiln_target.jsonnet_stdlib_dir is not None
        else {}
    )
    cfg = load_config(path, ProjectConfig, stdlibs)
    assert isinstance(cfg, ProjectConfig)
    return cfg


def test_load_json(tmp_path: Path):
    data = {
        "version": "1",
        "databases": [{"key": "primary", "default": True}],
        "apps": [
            {
                "config": {
                    "module": "myapp",
                    "resources": [
                        {
                            "model": "myapp.models.Widget",
                            "operations": [
                                {"name": "get"},
                                {"name": "list"},
                            ],
                        }
                    ],
                },
                "prefix": "",
            }
        ],
    }
    cfg_file = tmp_path / "kiln.json"
    cfg_file.write_text(json.dumps(data))

    cfg = _load(cfg_file)
    app = cfg.apps[0]
    assert app.config.module == "myapp"
    assert len(app.config.resources) == 1
    assert app.config.resources[0].model == "myapp.models.Widget"


def test_load_unsupported_extension(tmp_path: Path):
    bad = tmp_path / "kiln.yaml"
    bad.write_text("version: '1'")
    with pytest.raises(ConfigError, match="Unsupported"):
        _load(bad)


def test_load_jsonnet(tmp_path: Path):
    jsonnet_src = (
        "{ apps: [{ config: { module: 'jsonnet_app', resources: [] }, "
        "prefix: '' }], "
        "databases: [{ key: 'primary', default: true }] }"
    )
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = _load(cfg_file)
    assert cfg.apps[0].config.module == "jsonnet_app"


def test_load_jsonnet_relative_import(tmp_path: Path):
    helper = tmp_path / "helper.libsonnet"
    helper.write_text('{ mod: "helper_app" }')
    jsonnet_src = (
        'local h = import "helper.libsonnet"; '
        "{ apps: [{ config: { module: h.mod }, prefix: '' }], "
        "databases: [{ key: 'primary', default: true }] }"
    )
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = _load(cfg_file)
    assert cfg.apps[0].config.module == "helper_app"


def test_load_validation_error(tmp_path: Path):
    # model is required in ResourceConfig
    data = {"resources": [{"operations": [{"name": "get"}]}]}
    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text(json.dumps(data))

    with pytest.raises(ConfigError):
        _load(cfg_file)


def test_load_jsonnet_stdlib_resources(tmp_path: Path):
    src = """
    local resource = import "kiln/resources/presets.libsonnet";
    {
      databases: [{ key: "primary", default: true }],
      apps: [{
        config: {
          module: "blog",
          resources: [
            {
              model: "blog.models.Article",
              operations: [
                resource.action(
                  name="publish",
                  fn="blog.actions.publish",
                ),
              ],
            },
          ],
        },
        prefix: "",
      }],
    }
    """
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(src)
    cfg = _load(cfg_file)
    app = cfg.apps[0]
    assert app.config.module == "blog"
    assert len(app.config.resources) == 1
    assert app.config.resources[0].model == "blog.models.Article"
    operations = app.config.resources[0].operations
    assert operations is not None
    assert len(operations) == 1
    op = operations[0]
    assert not isinstance(op, str)
    assert op.name == "publish"
    assert op.type == "action"
    assert op.options == {"fn": "blog.actions.publish"}
    assert op.require_auth is True


def test_load_jsonnet_stdlib_resource_action_require_auth(tmp_path: Path):
    """``resource.action`` emits ``require_auth`` for bool values and
    omits the key only when explicitly passed ``null`` (inherit)."""
    src = """
    local resource = import "kiln/resources/presets.libsonnet";
    {
      databases: [{ key: "primary", default: true }],
      apps: [{
        config: {
          module: "blog",
          resources: [
            {
              model: "blog.models.Article",
              require_auth: false,
              operations: [
                resource.action(
                  name="publish",
                  fn="blog.actions.publish",
                  require_auth=false,
                ),
                resource.action(
                  name="archive",
                  fn="blog.actions.archive",
                  require_auth=null,
                ),
              ],
            },
          ],
        },
        prefix: "",
      }],
    }
    """
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(src)
    cfg = _load(cfg_file)
    ops = cfg.apps[0].config.resources[0].operations
    assert ops is not None
    publish, archive = ops[0], ops[1]
    assert not isinstance(publish, str)
    assert not isinstance(archive, str)
    assert publish.require_auth is False
    assert archive.require_auth is None
