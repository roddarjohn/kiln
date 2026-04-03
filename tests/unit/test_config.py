"""Tests for kiln config loading and schema validation."""

import json
from pathlib import Path

import pytest

from kiln.config.schema import (
    ActionConfig,
    AuthConfig,
    FieldsConfig,
    FieldSpec,
    KilnConfig,
    ResourceConfig,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_kiln_config_defaults():
    cfg = KilnConfig()
    assert cfg.version == "1"
    assert cfg.module == "app"
    assert cfg.auth is None
    assert cfg.resources == []


def test_auth_config_defaults():
    auth = AuthConfig()
    assert auth.type == "jwt"
    assert auth.secret_env == "JWT_SECRET"  # noqa: S105
    assert auth.algorithm == "HS256"
    assert "/docs" in auth.exclude_paths


def test_resource_config_defaults():
    r = ResourceConfig(model="myapp.models.User")
    assert r.pk == "id"
    assert r.pk_type == "uuid"
    assert r.route_prefix is None
    assert r.db_key is None
    assert r.require_auth is True
    assert r.get is False
    assert r.list is False
    assert r.create is False
    assert r.update is False
    assert r.delete is False
    assert r.actions == []


def test_resource_config_get_true():
    r = ResourceConfig(model="myapp.models.User", get=True)
    assert r.get is True


def test_resource_config_get_fields_config():
    r = ResourceConfig(
        model="myapp.models.User",
        get={
            "fields": [
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ]
        },
    )
    assert isinstance(r.get, FieldsConfig)
    assert len(r.get.fields) == 2
    assert r.get.fields[0].name == "id"
    assert r.get.fields[1].type == "email"


def test_resource_config_require_auth_list():
    r = ResourceConfig(
        model="myapp.models.User",
        require_auth=["create", "update", "delete"],
    )
    assert r.require_auth == ["create", "update", "delete"]


def test_resource_config_require_auth_false():
    r = ResourceConfig(model="myapp.models.User", require_auth=False)
    assert r.require_auth is False


def test_resource_config_custom_route_prefix():
    r = ResourceConfig(model="myapp.models.User", route_prefix="/people")
    assert r.route_prefix == "/people"


def test_resource_config_int_pk():
    r = ResourceConfig(model="myapp.models.Tag", pk="id", pk_type="int")
    assert r.pk_type == "int"


def test_action_config():
    a = ActionConfig(
        name="publish",
        fn="myapp.actions.publish",
        params=[FieldSpec(name="notify", type="bool")],
    )
    assert a.fn == "myapp.actions.publish"
    assert a.params[0].name == "notify"
    assert a.require_auth is True


def test_resource_config_with_actions():
    r = ResourceConfig(
        model="blog.models.Article",
        actions=[
            ActionConfig(
                name="publish",
                fn="blog.actions.publish_article",
            ),
        ],
    )
    assert len(r.actions) == 1
    assert r.actions[0].name == "publish"


def test_field_spec():
    f = FieldSpec(name="title", type="str")
    assert f.name == "title"
    assert f.type == "str"


def test_fields_config():
    fc = FieldsConfig(fields=[FieldSpec(name="id", type="uuid")])
    assert len(fc.fields) == 1


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
                "get": True,
                "list": True,
            }
        ],
    }
    cfg_file = tmp_path / "kiln.json"
    cfg_file.write_text(json.dumps(data))
    from kiln.config.loader import load

    cfg = load(cfg_file)
    assert cfg.module == "myapp"
    assert len(cfg.resources) == 1
    assert cfg.resources[0].model == "myapp.models.Widget"


def test_load_unsupported_extension(tmp_path: Path):
    from kiln.config.loader import load

    bad = tmp_path / "kiln.yaml"
    bad.write_text("version: '1'")
    with pytest.raises(ValueError, match="Unsupported"):
        load(bad)


def test_load_jsonnet(tmp_path: Path):
    from kiln.config.loader import load

    jsonnet_src = '{ module: "jsonnet_app", resources: [] }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.module == "jsonnet_app"


def test_load_jsonnet_relative_import(tmp_path: Path):
    from kiln.config.loader import load

    helper = tmp_path / "helper.libsonnet"
    helper.write_text('{ mod: "helper_app" }')
    jsonnet_src = 'local h = import "helper.libsonnet"; { module: h.mod }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.module == "helper_app"


def test_load_validation_error(tmp_path: Path):
    from kiln.config.loader import load

    # model is required in ResourceConfig
    data = {"resources": [{"get": True}]}
    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text(json.dumps(data))
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load(cfg_file)


def test_load_jsonnet_stdlib_resources(tmp_path: Path):
    from kiln.config.loader import load

    src = """
    local resource = import "kiln/resources/presets.libsonnet";
    {
      module: "blog",
      resources: [
        resource.read_only("blog.models.Article"),
      ],
    }
    """
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(src)
    cfg = load(cfg_file)
    assert cfg.module == "blog"
    assert len(cfg.resources) == 1
    assert cfg.resources[0].model == "blog.models.Article"
    assert cfg.resources[0].get is True
    assert cfg.resources[0].create is False
