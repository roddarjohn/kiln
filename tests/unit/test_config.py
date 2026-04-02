"""Tests for kiln config loading and schema validation."""

import json
from pathlib import Path

import pytest

from kiln.config.loader import load
from kiln.config.schema import (
    AuthConfig,
    CrudConfig,
    FieldConfig,
    KilnConfig,
    ModelConfig,
    ViewModel,
    ViewParam,
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


def test_model_config_schema_default():
    m = ModelConfig(
        name="User",
        table="users",
        fields=[FieldConfig(name="id", type="uuid", primary_key=True)],
    )
    assert m.schema == "public"
    assert m.pgcraft_type == "simple"


def test_crud_config_defaults():
    c = CrudConfig()
    assert c.create is True
    assert c.paginated is True
    assert c.require_auth == []


def test_view_model_is_parameterised():
    v = ViewModel(
        name="summary",
        model="Post",
        parameters=[ViewParam(name="start_date", type="date")],
        returns=[],
    )
    assert v.parameters != []


def test_view_model_non_parameterised():
    v = ViewModel(name="stats", model="User", returns=[])
    assert v.parameters == []


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
    cfg = load(cfg_file)
    assert cfg.module == "myapp"
    assert len(cfg.models) == 1
    assert cfg.models[0].name == "Widget"


def test_load_unsupported_extension(tmp_path: Path):
    bad = tmp_path / "kiln.yaml"
    bad.write_text("version: '1'")
    with pytest.raises(ValueError, match="Unsupported"):
        load(bad)


def test_load_jsonnet(tmp_path: Path):
    # Minimal inline jsonnet (no kiln/ stdlib imports needed).
    jsonnet_src = '{ module: "jsonnet_app", models: [] }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.module == "jsonnet_app"


def test_load_jsonnet_relative_import(tmp_path: Path):
    # Tests the non-kiln/ branch of _import_callback.
    helper = tmp_path / "helper.libsonnet"
    helper.write_text('{ mod: "helper_app" }')
    jsonnet_src = 'local h = import "helper.libsonnet"; { module: h.mod }'
    cfg_file = tmp_path / "kiln.jsonnet"
    cfg_file.write_text(jsonnet_src)
    cfg = load(cfg_file)
    assert cfg.module == "helper_app"


def test_load_validation_error(tmp_path: Path):
    # missing required 'fields' on model
    data = {
        "models": [{"name": "Bad", "table": "bad"}],
    }
    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text(json.dumps(data))
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load(cfg_file)
