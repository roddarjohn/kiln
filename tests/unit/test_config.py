"""Tests for kiln config loading and schema validation."""

import json
from pathlib import Path

import pytest

from kiln.config.schema import (
    ActionRouteConfig,
    AuthConfig,
    CrudConfig,
    CRUDRouteConfig,
    FieldConfig,
    KilnConfig,
    ModelConfig,
    ViewColumn,
    ViewConfig,
    ViewParam,
    ViewRouteConfig,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_kiln_config_defaults():
    cfg = KilnConfig()
    assert cfg.version == "1"
    assert cfg.module == "app"
    assert cfg.auth is None
    assert cfg.models == []
    assert cfg.views == []
    assert cfg.routes == []


def test_auth_config_defaults():
    auth = AuthConfig()
    assert auth.type == "jwt"
    assert auth.secret_env == "JWT_SECRET"  # noqa: S105
    assert auth.algorithm == "HS256"
    assert "/docs" in auth.exclude_paths


def test_field_config_primary_key():
    f = FieldConfig(name="id", type="uuid", primary_key=True)
    assert f.primary_key is True
    assert f.nullable is False
    assert f.exclude_from_api is False


def test_field_config_primary_key_dotted_path():
    f = FieldConfig(
        name="id",
        type="uuid",
        primary_key="pgcraft.plugins.pk.UUIDV7PKPlugin",
    )
    assert f.primary_key == "pgcraft.plugins.pk.UUIDV7PKPlugin"


def test_model_config_schema_default():
    m = ModelConfig(
        name="User",
        table="users",
        fields=[FieldConfig(name="id", type="uuid", primary_key=True)],
    )
    assert m.schema == "public"
    assert m.pgcraft_type == "pgcraft.factory.dimension.simple.PGCraftSimple"


def test_model_config_no_crud_field():
    """ModelConfig no longer has a crud field."""
    m = ModelConfig(
        name="User",
        table="users",
        fields=[FieldConfig(name="id", type="uuid", primary_key=True)],
    )
    assert not hasattr(m, "crud")


def test_model_config_no_db_key_field():
    """ModelConfig no longer has a db_key field."""
    m = ModelConfig(
        name="User",
        table="users",
        fields=[FieldConfig(name="id", type="uuid", primary_key=True)],
    )
    assert not hasattr(m, "db_key")


def test_crud_config_defaults():
    c = CrudConfig()
    assert c.create is True
    assert c.paginated is True
    assert c.require_auth == []


def test_view_config_is_parameterised():
    v = ViewConfig(
        name="summary",
        parameters=[ViewParam(name="start_date", type="date")],
        returns=[],
    )
    assert v.parameters != []


def test_view_config_non_parameterised():
    v = ViewConfig(name="stats", returns=[])
    assert v.parameters == []


def test_view_config_no_http_fields():
    """ViewConfig is a DB-layer model — no HTTP config fields."""
    v = ViewConfig(name="stats", returns=[])
    assert not hasattr(v, "require_auth")
    assert not hasattr(v, "http_method")
    assert not hasattr(v, "query_fn")


def test_crud_route_config():
    r = CRUDRouteConfig(model="User", crud=CrudConfig())
    assert r.type == "crud"
    assert r.model == "User"
    assert r.db_key is None


def test_view_route_config_defaults():
    r = ViewRouteConfig(view="my_view")
    assert r.type == "view"
    assert r.http_method == "GET"
    assert r.require_auth is True
    assert r.description == ""


def test_action_route_config():
    r = ActionRouteConfig(
        name="publish_article",
        fn="public.publish_article",
        params=[ViewParam(name="article_id", type="uuid")],
        returns=[ViewColumn(name="status", type="str")],
    )
    assert r.type == "action"
    assert r.fn == "public.publish_article"


def test_route_config_discriminated_union():
    """Routes list uses a discriminated union on the 'type' field."""
    cfg = KilnConfig(
        routes=[
            {"type": "crud", "model": "User", "crud": {}},
            {"type": "view", "view": "my_view"},
            {
                "type": "action",
                "name": "do_thing",
                "fn": "public.do_thing",
            },
        ]
    )
    assert isinstance(cfg.routes[0], CRUDRouteConfig)
    assert isinstance(cfg.routes[1], ViewRouteConfig)
    assert isinstance(cfg.routes[2], ActionRouteConfig)


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


def test_load_json(tmp_path: Path):
    data = {
        "version": "1",
        "module": "myapp",
        "models": [
            {
                "name": "Widget",
                "table": "widgets",
                "fields": [{"name": "id", "type": "int", "primary_key": True}],
            }
        ],
    }
    cfg_file = tmp_path / "kiln.json"
    cfg_file.write_text(json.dumps(data))
    from kiln.config.loader import load

    cfg = load(cfg_file)
    assert cfg.module == "myapp"
    assert len(cfg.models) == 1
    assert cfg.models[0].name == "Widget"


def test_load_unsupported_extension(tmp_path: Path):
    from kiln.config.loader import load

    bad = tmp_path / "kiln.yaml"
    bad.write_text("version: '1'")
    with pytest.raises(ValueError, match="Unsupported"):
        load(bad)


def test_load_jsonnet(tmp_path: Path):
    from kiln.config.loader import load

    # Minimal inline jsonnet (no kiln/ stdlib imports needed).
    jsonnet_src = '{ module: "jsonnet_app", models: [] }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.module == "jsonnet_app"


def test_load_jsonnet_relative_import(tmp_path: Path):
    from kiln.config.loader import load

    # Tests the non-kiln/ branch of _import_callback.
    helper = tmp_path / "helper.libsonnet"
    helper.write_text('{ mod: "helper_app" }')
    jsonnet_src = 'local h = import "helper.libsonnet"; { module: h.mod }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.module == "helper_app"


def test_load_validation_error(tmp_path: Path):
    from kiln.config.loader import load

    # missing required 'fields' on model
    data = {
        "models": [{"name": "Bad", "table": "bad"}],
    }
    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text(json.dumps(data))
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load(cfg_file)
