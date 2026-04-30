"""Tests for be config loading and schema validation."""

import json
from typing import TYPE_CHECKING

import pytest

from be.config.schema import (
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    FieldSpec,
    FilterConfig,
    OperationConfig,
    ProjectConfig,
    RepresentationConfig,
    ResourceConfig,
    StructuredFilterField,
)
from foundry.config import load_config
from foundry.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path

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


def _project_with(
    resource: ResourceConfig, **kwargs: object
) -> dict[str, object]:
    """Build the kwargs for a ProjectConfig with one resource."""
    return {
        "databases": [DatabaseConfig(key="primary", default=True)],
        "apps": [
            {
                "config": {
                    "module": "app",
                    "resources": [resource.model_dump()],
                },
                "prefix": "",
            },
        ],
        **kwargs,
    }


def test_project_config_dump_flag_without_auth_rejected():
    from pydantic import ValidationError

    resource = ResourceConfig(
        model="app.models.Post", include_actions_in_dump=True
    )

    with pytest.raises(ValidationError, match="include_actions_in_dump"):
        ProjectConfig(**_project_with(resource))


def test_project_config_permissions_endpoint_without_auth_rejected():
    from pydantic import ValidationError

    resource = ResourceConfig(
        model="app.models.Post", permissions_endpoint=True
    )

    with pytest.raises(ValidationError, match="permissions_endpoint"):
        ProjectConfig(**_project_with(resource))


def test_project_config_can_on_op_without_auth_rejected():
    from pydantic import ValidationError

    resource = ResourceConfig(
        model="app.models.Post",
        operations=[
            OperationConfig(name="get", can="app.guards.can_get_post"),
        ],
    )

    with pytest.raises(ValidationError, match="can="):
        ProjectConfig(**_project_with(resource))


def test_project_config_action_framework_with_auth_validates():
    """Same opt-ins succeed when auth is configured."""
    resource = ResourceConfig(
        model="app.models.Post",
        include_actions_in_dump=True,
        permissions_endpoint=True,
        operations=[
            OperationConfig(name="get", can="app.guards.can_get_post"),
        ],
    )
    cfg = ProjectConfig(
        **_project_with(
            resource,
            auth=AuthConfig(
                credentials_schema="app.auth.LoginCredentials",
                session_schema="app.auth.Session",
                validate_fn="app.auth.validate",
            ),
        ),
    )
    assert cfg.auth is not None


def test_resource_config_defaults():
    r = ResourceConfig(model="myapp.models.User")
    assert r.pk.name == "id"
    assert r.pk.type == "uuid"
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
    r = ResourceConfig(
        model="myapp.models.Tag",
        pk={"name": "id", "type": "int"},
    )
    assert r.pk.type == "int"
    assert r.pk.name == "id"


def test_resource_config_pk_defaults_to_id_uuid():
    r = ResourceConfig(model="myapp.models.Article")
    assert r.pk.name == "id"
    assert r.pk.type == "uuid"


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


def test_operation_config_can_defaults_to_none():
    oc = OperationConfig(name="get")
    assert oc.can is None


def test_operation_config_can_dotted_path():
    oc = OperationConfig(name="publish", can="myapp.guards.can_publish")
    assert oc.can == "myapp.guards.can_publish"
    assert "can" not in oc.options


def test_operation_config_hooks_default_to_none():
    oc = OperationConfig(name="get")
    assert oc.pre is None
    assert oc.post is None


def test_operation_config_hooks_on_create_allowed():
    oc = OperationConfig(
        name="create",
        pre="myapp.hooks.before_create",
        post="myapp.hooks.after_create",
    )
    assert oc.pre == "myapp.hooks.before_create"
    assert oc.post == "myapp.hooks.after_create"
    assert "pre" not in oc.options
    assert "post" not in oc.options


def test_operation_config_hooks_on_update_allowed():
    oc = OperationConfig(
        name="update",
        pre="myapp.hooks.before_update",
        post="myapp.hooks.after_update",
    )
    assert oc.pre == "myapp.hooks.before_update"
    assert oc.post == "myapp.hooks.after_update"


@pytest.mark.parametrize("op_name", ["get", "delete", "list"])
def test_operation_config_hooks_rejected_on_read_only_ops(op_name):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="hooks are only"):
        OperationConfig(name=op_name, pre="myapp.hooks.h")

    with pytest.raises(ValidationError, match="hooks are only"):
        OperationConfig(name=op_name, post="myapp.hooks.h")

    with pytest.raises(ValidationError, match="hooks are only"):
        OperationConfig(name=op_name, dump="myapp.hooks.dump_body")


def test_operation_config_hooks_rejected_on_action_ops():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="not supported on action ops"):
        OperationConfig(
            name="publish",
            type="action",
            fn="myapp.actions.publish",
            pre="myapp.hooks.h",
        )

    with pytest.raises(ValidationError, match="not supported on action ops"):
        OperationConfig(
            name="publish",
            type="action",
            fn="myapp.actions.publish",
            post="myapp.hooks.h",
        )


def test_resource_config_action_flags_default_false():
    r = ResourceConfig(model="myapp.models.User")
    assert r.include_actions_in_dump is False
    assert r.permissions_endpoint is False


def test_resource_config_action_flags_overridable():
    r = ResourceConfig(
        model="myapp.models.User",
        include_actions_in_dump=True,
        permissions_endpoint=True,
    )
    assert r.include_actions_in_dump is True
    assert r.permissions_endpoint is True


def test_resource_config_reserved_actions_field_rejected():
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError, match="reserves the field name 'actions'"
    ):
        ResourceConfig(
            model="myapp.models.User",
            include_actions_in_dump=True,
            operations=[
                {
                    "name": "get",
                    "fields": [
                        {"name": "id", "type": "uuid"},
                        {"name": "actions", "type": "str"},
                    ],
                },
            ],
        )


def test_resource_config_actions_field_allowed_when_dump_disabled():
    r = ResourceConfig(
        model="myapp.models.User",
        operations=[
            {
                "name": "get",
                "fields": [
                    {"name": "actions", "type": "str"},
                ],
            },
        ],
    )
    assert r.include_actions_in_dump is False


def test_resource_config_dump_with_unrelated_fields_validates():
    r = ResourceConfig(
        model="myapp.models.User",
        include_actions_in_dump=True,
        operations=[
            {
                "name": "get",
                "fields": [
                    {"name": "id", "type": "uuid"},
                    {"name": "email", "type": "email"},
                ],
            },
        ],
    )
    assert r.include_actions_in_dump is True


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


def test_field_spec_nested_with_enum_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="`enum` is only allowed when"):
        FieldSpec(
            name="project",
            type="nested",
            model="blog.models.Project",
            fields=[FieldSpec(name="id", type="uuid")],
            enum="blog.models.Status",
        )


def test_field_spec_enum_requires_enum_class() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="requires"):
        FieldSpec(name="status", type="enum")


def test_field_spec_enum_with_nested_fields_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError, match='only allowed when `type: "nested"`'
    ):
        FieldSpec(
            name="status",
            type="enum",
            enum="blog.models.Status",
            many=True,
        )


def test_representation_builder_with_fields_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="provide either `builder` or"):
        RepresentationConfig(
            name="default",
            fields=[{"name": "title", "type": "str"}],
            builder="app.links.build_project_link",
        )


def test_representation_fields_only_is_valid() -> None:
    cfg = RepresentationConfig(
        name="default",
        fields=[
            {"name": "id", "type": "uuid"},
            {"name": "title", "type": "str"},
        ],
    )
    assert cfg.builder is None
    assert [f.name for f in cfg.fields] == ["id", "title"]
    assert [f.type for f in cfg.fields] == ["uuid", "str"]


def test_representation_builder_only_is_valid() -> None:
    cfg = RepresentationConfig(name="default", builder="app.links.build_link")
    assert cfg.builder == "app.links.build_link"
    assert cfg.fields == []


def test_representation_empty_fields_is_valid() -> None:
    # Empty fields = schema with just the ``type`` discriminator;
    # occasionally useful for ``ref`` filters that only need the
    # resource-type identity.
    cfg = RepresentationConfig(name="lite")
    assert cfg.fields == []
    assert cfg.builder is None


def test_resource_default_representation_must_be_declared() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="default_representation="):
        ResourceConfig(
            model="app.models.Item",
            representations=[
                RepresentationConfig(name="lite"),
            ],
            default_representation="missing",
        )


def test_resource_representation_names_must_be_unique() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="duplicate representation"):
        ResourceConfig(
            model="app.models.Item",
            representations=[
                RepresentationConfig(name="default"),
                RepresentationConfig(name="default"),
            ],
        )


def test_searchable_resource_without_default_representation_rejected() -> None:
    from pydantic import ValidationError

    resource = ResourceConfig(model="app.models.Item", searchable=True)

    with pytest.raises(
        ValidationError,
        match=r"requires `default_representation`.*searchable=True",
    ):
        ProjectConfig(
            **_project_with(
                resource,
                auth=AuthConfig(
                    credentials_schema="app.auth.LoginCredentials",
                    session_schema="app.auth.Session",
                    validate_fn="app.auth.validate",
                ),
            ),
        )


def test_resource_referenced_by_ref_without_default_rep_rejected() -> None:
    from pydantic import ValidationError

    target = ResourceConfig(model="app.models.Customer")
    referrer = ResourceConfig(
        model="app.models.Order",
        operations=[
            OperationConfig(
                name="list",
                modifiers=[
                    {
                        "type": "filter",
                        "fields": [
                            {
                                "name": "customer_id",
                                "values": "ref",
                                "ref_resource": "customer",
                            },
                        ],
                    },
                ],
            ),
        ],
    )

    with pytest.raises(
        ValidationError,
        match=r"requires `default_representation`.*ref_resource",
    ):
        ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            apps=[
                {
                    "config": {
                        "module": "app",
                        "resources": [
                            target.model_dump(),
                            referrer.model_dump(),
                        ],
                    },
                    "prefix": "",
                },
            ],
        )


def test_self_filter_without_default_representation_rejected() -> None:
    from pydantic import ValidationError

    resource = ResourceConfig(
        model="app.models.Item",
        operations=[
            OperationConfig(
                name="list",
                modifiers=[
                    {
                        "type": "filter",
                        "fields": [{"name": "id", "values": "self"}],
                    },
                ],
            ),
        ],
    )

    with pytest.raises(
        ValidationError, match=r'requires `default_representation`.*"self"'
    ):
        ProjectConfig(**_project_with(resource))


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


def _load(path: Path) -> ProjectConfig:
    from be.target import target as kiln_target

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
    cfg_file = tmp_path / "be.json"
    cfg_file.write_text(json.dumps(data))

    cfg = _load(cfg_file)
    app = cfg.apps[0]
    assert app.config.module == "myapp"
    assert len(app.config.resources) == 1
    assert app.config.resources[0].model == "myapp.models.Widget"


def test_load_unsupported_extension(tmp_path: Path):
    bad = tmp_path / "be.yaml"
    bad.write_text("version: '1'")

    with pytest.raises(ConfigError, match="Unsupported"):
        _load(bad)


def test_load_jsonnet(tmp_path: Path):
    jsonnet_src = (
        "{ apps: [{ config: { module: 'jsonnet_app', resources: [] }, "
        "prefix: '' }], "
        "databases: [{ key: 'primary', default: true }] }"
    )
    cfg_file = tmp_path / "be.jsonnet"
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
    cfg_file = tmp_path / "be.jsonnet"
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
    local resource = import "be/resources/presets.libsonnet";
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
    cfg_file = tmp_path / "be.jsonnet"
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
    local resource = import "be/resources/presets.libsonnet";
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
    cfg_file = tmp_path / "be.jsonnet"
    cfg_file.write_text(src)
    cfg = _load(cfg_file)
    ops = cfg.apps[0].config.resources[0].operations
    assert ops is not None
    publish, archive = ops[0], ops[1]
    assert not isinstance(publish, str)
    assert not isinstance(archive, str)
    assert publish.require_auth is False
    assert archive.require_auth is None


# ---------------------------------------------------------------------------
# StructuredFilterField + FilterConfig
# ---------------------------------------------------------------------------


def test_structured_filter_enum_defaults():
    field = StructuredFilterField(
        name="status", values="enum", enum="myapp.OrderStatus"
    )
    assert field.operators == ["eq", "in"]
    assert field.enum == "myapp.OrderStatus"


def test_structured_filter_bool_defaults():
    field = StructuredFilterField(name="archived", values="bool")
    assert field.operators == ["eq"]


def test_structured_filter_ref_defaults():
    field = StructuredFilterField(
        name="customer_id", values="ref", ref_resource="customer"
    )
    assert field.operators == ["eq", "in"]


def test_structured_filter_free_text_defaults():
    field = StructuredFilterField(name="sku", values="free_text")
    assert field.operators == ["eq", "contains", "starts_with"]


def test_structured_filter_literal_defaults():
    field = StructuredFilterField(
        name="created_at", values="literal", type="datetime"
    )
    assert field.operators == ["eq", "gt", "gte", "lt", "lte"]


def test_structured_filter_explicit_operators_kept():
    field = StructuredFilterField(
        name="status",
        values="enum",
        enum="myapp.OrderStatus",
        operators=["eq"],
    )
    assert field.operators == ["eq"]


def test_structured_filter_unknown_operator_rejected():
    # Pydantic's Literal validation rejects unknown operators
    # before any custom validator runs.
    with pytest.raises(ValueError, match="literal_error"):
        StructuredFilterField(
            name="status",
            values="enum",
            enum="myapp.OrderStatus",
            operators=["nonsense"],
        )


def test_structured_filter_enum_requires_enum_path():
    with pytest.raises(ValueError, match="requires `enum`"):
        StructuredFilterField(name="status", values="enum")


def test_structured_filter_literal_requires_type():
    with pytest.raises(ValueError, match="requires `type`"):
        StructuredFilterField(name="created_at", values="literal")


def test_structured_filter_ref_requires_ref_resource():
    with pytest.raises(ValueError, match="requires `ref_resource`"):
        StructuredFilterField(name="customer_id", values="ref")


def test_structured_filter_enum_rejects_type():
    with pytest.raises(ValueError, match="`type` is not allowed"):
        StructuredFilterField(
            name="status",
            values="enum",
            enum="myapp.OrderStatus",
            type="str",
        )


def test_structured_filter_bool_rejects_enum():
    with pytest.raises(ValueError, match="`enum` is not allowed"):
        StructuredFilterField(name="archived", values="bool", enum="myapp.X")


def test_structured_filter_free_text_rejects_ref_resource():
    with pytest.raises(ValueError, match="`ref_resource` is not allowed"):
        StructuredFilterField(
            name="sku", values="free_text", ref_resource="customer"
        )


def test_filter_config_requires_structured_fields():
    """Filter config rejects bare-string entries; only StructuredFilterField."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FilterConfig.model_validate({"fields": ["sku", "name"]})


def test_filter_config_requires_at_least_one_field():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FilterConfig(fields=[])


def test_filter_config_passes_structured_through():
    cfg = FilterConfig.model_validate(
        {
            "fields": [
                {
                    "name": "status",
                    "values": "enum",
                    "enum": "myapp.OrderStatus",
                },
            ]
        }
    )
    assert len(cfg.fields) == 1
    assert isinstance(cfg.fields[0], StructuredFilterField)
    assert cfg.fields[0].operators == ["eq", "in"]
    assert cfg.fields[0].enum == "myapp.OrderStatus"


def test_structured_filter_self_defaults():
    field = StructuredFilterField(name="id", values="self")
    assert field.operators == ["eq", "in"]


def test_structured_filter_self_rejects_other_fields():
    with pytest.raises(ValueError, match="not allowed"):
        StructuredFilterField(name="id", values="self", ref_resource="customer")
