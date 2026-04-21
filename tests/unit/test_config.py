"""Tests for kiln config loading and schema validation."""

import json
from pathlib import Path

import pytest

from kiln.config.schema import (
    AppConfig,
    AuthConfig,
    FieldSpec,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_project_config_defaults():
    cfg = ProjectConfig()
    assert cfg.version == "1"
    assert cfg.auth is None
    assert cfg.apps == []


def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.module == "app"
    assert cfg.resources == []
    assert cfg.operations is None


def test_project_config_shorthand_wraps_single_app():
    cfg = ProjectConfig.model_validate(
        {
            "module": "myapp",
            "resources": [{"model": "myapp.models.Post"}],
            "operations": ["get", "list"],
        }
    )
    assert len(cfg.apps) == 1
    app = cfg.apps[0]
    assert app.prefix == ""
    assert app.config.module == "myapp"
    assert app.config.resources[0].model == "myapp.models.Post"
    assert app.config.operations == ["get", "list"]


def test_project_config_apps_mode_untouched():
    cfg = ProjectConfig.model_validate(
        {
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
        verify_credentials_fn="myapp.auth.verify",
    )
    assert auth.type == "jwt"
    assert auth.secret_env == "JWT_SECRET"  # noqa: S105
    assert auth.algorithm == "HS256"
    assert "/docs" in auth.exclude_paths


def test_auth_config_verify_credentials_required():
    with pytest.raises(ValueError, match="verify_credentials_fn"):
        AuthConfig()


def test_auth_config_verify_not_required_with_custom_auth():
    auth = AuthConfig(
        get_current_user_fn="myapp.auth.get_user",
    )
    assert auth.verify_credentials_fn is None


def test_resource_config_defaults():
    r = ResourceConfig(model="myapp.models.User")
    assert r.pk == "id"
    assert r.pk_type == "uuid"
    assert r.route_prefix is None
    assert r.db_key is None
    assert r.require_auth is True
    assert r.operations is None


def test_resource_config_with_string_operations():
    r = ResourceConfig(
        model="myapp.models.User",
        operations=["get", "list"],
    )
    assert len(r.operations) == 2
    assert r.operations[0] == "get"
    assert r.operations[1] == "list"


def test_resource_config_with_operation_configs():
    r = ResourceConfig(
        model="myapp.models.User",
        operations=[
            "get",
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
    assert r.operations[0] == "get"
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
        fn="blog.actions.publish",
        params=[{"name": "notify", "type": "bool"}],
    )
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


def test_app_config_with_operations():
    cfg = AppConfig(
        operations=["get", "list", "create"],
    )
    assert len(cfg.operations) == 3


def test_field_spec():
    f = FieldSpec(name="title", type="str")
    assert f.name == "title"
    assert f.type == "str"


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


def test_load_json(tmp_path: Path):
    data = {
        "version": "1",
        "module": "myapp",
        "resources": [
            {
                "model": "myapp.models.Widget",
                "operations": ["get", "list"],
            }
        ],
    }
    cfg_file = tmp_path / "kiln.json"
    cfg_file.write_text(json.dumps(data))
    from kiln.config.loader import load

    cfg = load(cfg_file)
    app = cfg.apps[0]
    assert app.config.module == "myapp"
    assert len(app.config.resources) == 1
    assert app.config.resources[0].model == "myapp.models.Widget"


def test_load_unsupported_extension(tmp_path: Path):
    from kiln.config.loader import load
    from kiln.errors import ConfigError

    bad = tmp_path / "kiln.yaml"
    bad.write_text("version: '1'")
    with pytest.raises(ConfigError, match="Unsupported"):
        load(bad)


def test_load_jsonnet(tmp_path: Path):
    from kiln.config.loader import load

    jsonnet_src = '{ module: "jsonnet_app", resources: [] }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.apps[0].config.module == "jsonnet_app"


def test_load_jsonnet_relative_import(tmp_path: Path):
    from kiln.config.loader import load

    helper = tmp_path / "helper.libsonnet"
    helper.write_text('{ mod: "helper_app" }')
    jsonnet_src = 'local h = import "helper.libsonnet"; { module: h.mod }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.apps[0].config.module == "helper_app"


def test_load_validation_error(tmp_path: Path):
    from kiln.config.loader import load
    from kiln.errors import ConfigError

    # model is required in ResourceConfig
    data = {"resources": [{"operations": ["get"]}]}
    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text(json.dumps(data))

    with pytest.raises(ConfigError):
        load(cfg_file)


def test_load_jsonnet_stdlib_resources(tmp_path: Path):
    from kiln.config.loader import load

    src = """
    local resource = import "kiln/resources/presets.libsonnet";
    {
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
    }
    """
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(src)
    cfg = load(cfg_file)
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
    assert op.options == {"fn": "blog.actions.publish"}
