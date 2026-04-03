"""Tests for kiln code generators."""

import ast
from pathlib import Path

import pytest

from kiln.config.schema import (
    AppRef,
    AuthConfig,
    CrudConfig,
    CRUDRouteConfig,
    DatabaseConfig,
    FieldConfig,
    KilnConfig,
    ModelConfig,
    ViewColumn,
    ViewConfig,
    ViewParam,
    ViewRouteConfig,
)
from kiln.generators.base import GeneratedFile, Generator
from kiln.generators.fastapi.crud import CRUDGenerator
from kiln.generators.fastapi.models import PGCraftModelGenerator
from kiln.generators.fastapi.project_router import ProjectRouterGenerator
from kiln.generators.fastapi.router import RouterGenerator
from kiln.generators.fastapi.views import ViewGenerator
from kiln.generators.init.scaffold import ScaffoldGenerator
from kiln.generators.registry import GeneratorRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_model() -> ModelConfig:
    return ModelConfig(
        name="User",
        table="users",
        schema="public",
        pgcraft_type="pgcraft.factory.dimension.simple.PGCraftSimple",
        fields=[
            FieldConfig(name="id", type="uuid", primary_key=True),
            FieldConfig(name="email", type="email", unique=True),
            FieldConfig(
                name="hashed_password", type="str", exclude_from_api=True
            ),
            FieldConfig(name="created_at", type="datetime", auto_now_add=True),
        ],
    )


@pytest.fixture
def parameterised_view() -> ViewConfig:
    return ViewConfig(
        name="summarize_posts_by_user",
        schema="public",
        parameters=[
            ViewParam(name="start_date", type="date"),
            ViewParam(name="end_date", type="date"),
        ],
        returns=[
            ViewColumn(name="user_id", type="uuid"),
            ViewColumn(name="post_count", type="int"),
        ],
    )


@pytest.fixture
def plain_view() -> ViewConfig:
    return ViewConfig(
        name="active_users",
        schema="public",
        parameters=[],
        returns=[
            ViewColumn(name="id", type="uuid"),
            ViewColumn(name="email", type="str"),
        ],
    )


@pytest.fixture
def full_config(simple_model, parameterised_view) -> KilnConfig:
    return KilnConfig(
        module="app",
        auth=AuthConfig(),
        models=[simple_model],
        views=[parameterised_view],
        routes=[
            CRUDRouteConfig(
                model="User",
                crud=CrudConfig(require_auth=["update", "delete"]),
            ),
            ViewRouteConfig(view="summarize_posts_by_user"),
        ],
    )


# ---------------------------------------------------------------------------
# GeneratedFile + Generator protocol
# ---------------------------------------------------------------------------


def test_generated_file_defaults():
    f = GeneratedFile(path="foo.py", content="# hi")
    assert f.overwrite is True


def test_generated_file_no_overwrite():
    f = GeneratedFile(path="stub.py", content="# stub", overwrite=False)
    assert f.overwrite is False


def test_generator_protocol():
    assert isinstance(PGCraftModelGenerator(), Generator)
    assert isinstance(CRUDGenerator(), Generator)
    assert isinstance(ViewGenerator(), Generator)
    assert isinstance(RouterGenerator(), Generator)


# ---------------------------------------------------------------------------
# ScaffoldGenerator
# ---------------------------------------------------------------------------


def test_scaffold_generates_db_files():
    cfg = KilnConfig()
    files = ScaffoldGenerator().generate(cfg)
    paths = {f.path for f in files}
    assert "db/base.py" in paths
    assert "db/session.py" in paths
    assert "auth/dependencies.py" not in paths  # no auth in config


def test_scaffold_with_auth_generates_deps():
    cfg = KilnConfig(auth=AuthConfig())
    files = ScaffoldGenerator().generate(cfg)
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths


def test_scaffold_all_files_overwriteable():
    cfg = KilnConfig(auth=AuthConfig())
    for f in ScaffoldGenerator().generate(cfg):
        assert f.overwrite is True, f"{f.path} should have overwrite=True"


def test_scaffold_auth_deps_valid_python():
    cfg = KilnConfig(auth=AuthConfig())
    files = {f.path: f for f in ScaffoldGenerator().generate(cfg)}
    src = files["auth/dependencies.py"].content
    ast.parse(src)  # raises SyntaxError if invalid


def test_scaffold_auth_deps_injection():
    cfg = KilnConfig(
        auth=AuthConfig(
            get_current_user_fn="myapp.auth.custom.get_current_user"
        )
    )
    files = {f.path: f for f in ScaffoldGenerator().generate(cfg)}
    src = files["auth/dependencies.py"].content
    assert "from myapp.auth.custom import get_current_user" in src
    assert "jwt.decode" not in src


# ---------------------------------------------------------------------------
# PGCraftModelGenerator
# ---------------------------------------------------------------------------


def test_model_generator_can_generate(full_config):
    assert PGCraftModelGenerator().can_generate(full_config)


def test_model_generator_cannot_generate_empty():
    assert not PGCraftModelGenerator().can_generate(KilnConfig())


def test_model_generator_output_paths(full_config):
    files = PGCraftModelGenerator().generate(full_config)
    assert any(f.path == "app/models/user.py" for f in files)
    assert any(f.path == "app/models/__init__.py" for f in files)


def test_model_generator_valid_python(full_config):
    for f in PGCraftModelGenerator().generate(full_config):
        ast.parse(f.content)


def test_model_generator_contains_class(full_config, simple_model):
    files = PGCraftModelGenerator().generate(full_config)
    model_file = next(f for f in files if f.path == "app/models/user.py")
    assert f"class {simple_model.name}(Base):" in model_file.content


def test_model_generator_postgrest_plugin():
    m = ModelConfig(
        name="Item",
        table="items",
        pgcraft_plugins=["pgcraft.extensions.postgrest.PostgRESTPlugin"],
        fields=[FieldConfig(name="id", type="int", primary_key=True)],
    )
    cfg = KilnConfig(models=[m])
    files = PGCraftModelGenerator().generate(cfg)
    model_file = next(f for f in files if f.path == "app/models/item.py")
    assert "PostgRESTPlugin" in model_file.content
    expected_import = "from pgcraft.extensions.postgrest import PostgRESTPlugin"
    assert expected_import in model_file.content


# ---------------------------------------------------------------------------
# ViewGenerator
# ---------------------------------------------------------------------------


def test_view_generator_produces_route(full_config, parameterised_view):
    files = ViewGenerator().generate(full_config)
    paths = {f.path for f in files}
    assert f"app/routes/{parameterised_view.name}.py" in paths


def test_view_generator_no_stubs(full_config):
    files = ViewGenerator().generate(full_config)
    assert not any("stub" in f.path for f in files)


def test_view_route_overwrite(full_config, parameterised_view):
    files = ViewGenerator().generate(full_config)
    route = next(
        f for f in files if f.path == f"app/routes/{parameterised_view.name}.py"
    )
    assert route.overwrite is True


def test_view_route_valid_python(full_config):
    for f in ViewGenerator().generate(full_config):
        ast.parse(f.content)


def test_plain_view_route_uses_text_query(plain_view):
    cfg = KilnConfig(
        views=[plain_view],
        routes=[ViewRouteConfig(view="active_users", require_auth=False)],
    )
    files = ViewGenerator().generate(cfg)
    route = next(f for f in files if "routes/" in f.path)
    assert "text(" in route.content
    assert "table_valued" not in route.content


def test_function_view_route_uses_table_valued(parameterised_view):
    cfg = KilnConfig(
        views=[parameterised_view],
        routes=[ViewRouteConfig(view="summarize_posts_by_user")],
    )
    files = ViewGenerator().generate(cfg)
    route = next(f for f in files if "routes/" in f.path)
    assert "table_valued" in route.content
    assert "func." in route.content


# ---------------------------------------------------------------------------
# CRUDGenerator
# ---------------------------------------------------------------------------


def test_crud_generator_can_generate(full_config):
    assert CRUDGenerator().can_generate(full_config)


def test_crud_generator_skips_no_crud():
    m = ModelConfig(
        name="X", table="x", fields=[FieldConfig(name="id", type="int")]
    )
    assert not CRUDGenerator().can_generate(KilnConfig(models=[m]))


def test_crud_generator_output_path(full_config):
    files = CRUDGenerator().generate(full_config)
    assert any(f.path == "app/routes/user.py" for f in files)
    assert any(f.path == "app/schemas/user.py" for f in files)
    assert any(f.path == "app/schemas/__init__.py" for f in files)


def test_crud_generator_valid_python(full_config):
    for f in CRUDGenerator().generate(full_config):
        ast.parse(f.content)


def test_crud_generator_includes_auth(full_config):
    files = CRUDGenerator().generate(full_config)
    routes = next(f for f in files if f.path == "app/routes/user.py")
    # update and delete require auth in fixture
    assert "CurrentUser" in routes.content
    assert "get_current_user" in routes.content


def test_crud_generator_no_auth_when_unconfigured(simple_model):
    cfg = KilnConfig(
        models=[simple_model],
        routes=[CRUDRouteConfig(model="User", crud=CrudConfig())],
    )
    files = CRUDGenerator().generate(cfg)
    routes = next(f for f in files if "routes/user.py" in f.path)
    assert "get_current_user" not in routes.content


def test_crud_generator_schemas_present(full_config, simple_model):
    files = CRUDGenerator().generate(full_config)
    schema_file = next(f for f in files if f.path == "app/schemas/user.py")
    assert f"{simple_model.name}Create" in schema_file.content
    assert f"{simple_model.name}Update" in schema_file.content
    assert f"{simple_model.name}Response" in schema_file.content


def test_crud_generator_excluded_field_not_in_schema(full_config):
    files = CRUDGenerator().generate(full_config)
    schema_file = next(f for f in files if f.path == "app/schemas/user.py")
    # hashed_password is exclude_from_api=True
    assert "hashed_password" not in schema_file.content


# ---------------------------------------------------------------------------
# RouterGenerator
# ---------------------------------------------------------------------------


def test_router_generator_can_generate(full_config):
    assert RouterGenerator().can_generate(full_config)


def test_router_generator_output_path(full_config):
    files = RouterGenerator().generate(full_config)
    assert any(f.path == "app/routes/__init__.py" for f in files)


def test_router_generator_valid_python(full_config):
    (f,) = RouterGenerator().generate(full_config)
    ast.parse(f.content)


def test_router_generator_includes_all_routers(full_config):
    (f,) = RouterGenerator().generate(full_config)
    assert "user_router" in f.content
    assert "summarize_posts_by_user_router" in f.content


# ---------------------------------------------------------------------------
# GeneratorRegistry
# ---------------------------------------------------------------------------


def test_registry_default_has_builtins():
    r = GeneratorRegistry.default()
    names = set(r._generators)
    assert "pgcraft_models" in names
    assert "crud" in names
    assert "views" in names
    assert "router" in names


def test_registry_run_returns_files(full_config):
    files = GeneratorRegistry.default().run(full_config)
    assert len(files) > 0


def test_registry_custom_generator(full_config):
    class NoOpGenerator:
        @property
        def name(self) -> str:
            return "noop"

        def can_generate(self, _config: KilnConfig) -> bool:
            return True

        def generate(self, _config: KilnConfig) -> list[GeneratedFile]:
            return [GeneratedFile("noop.txt", "hi")]

    r = GeneratorRegistry()
    r.register(NoOpGenerator())
    files = r.run(full_config)
    assert any(f.path == "noop.txt" for f in files)


def test_registry_write_files(full_config, tmp_path: Path):
    from kiln.cli import _write_files

    files = GeneratorRegistry.default().run(full_config)
    written, skipped = _write_files(files, tmp_path)
    assert written > 0
    assert skipped == 0
    written2, skipped2 = _write_files(files, tmp_path)
    assert written2 == written  # all files overwritten again (none are stubs)
    assert skipped2 == 0


# ---------------------------------------------------------------------------
# _helpers — column_def branches
# ---------------------------------------------------------------------------


def test_column_def_foreign_key():
    from kiln.generators._helpers import column_def

    f = FieldConfig(
        name="author_id", type="uuid", foreign_key="authors.id", nullable=True
    )
    result = column_def(f)
    assert 'PGCraftForeignKey("authors.id")' in result
    assert "nullable=True" in result


def test_column_def_foreign_key_three_part_strips_schema():
    """Three-part schema.table.column refs are converted to table.column.

    pgcraft resolves FKs via its dimension registry using two-part
    'table.column' references; three-part refs bypass the registry and
    reference the raw SQLAlchemy table name (e.g. 'products_raw'), not
    the logical name ('products').
    """
    from kiln.generators._helpers import column_def

    f = FieldConfig(
        name="product_id",
        type="uuid",
        foreign_key="inventory.products.id",
        nullable=False,
    )
    result = column_def(f)
    assert 'PGCraftForeignKey("products.id")' in result


def test_column_def_auto_now_add_and_auto_now():
    from kiln.generators._helpers import column_def

    f1 = FieldConfig(name="created_at", type="datetime", auto_now_add=True)
    assert "server_default=func.now()" in column_def(f1)

    f2 = FieldConfig(name="updated_at", type="datetime", auto_now=True)
    assert "onupdate=func.now()" in column_def(f2)


def test_type_imports_json_and_date():
    from kiln.generators._helpers import type_imports

    result = type_imports(["json", "date"])
    assert "from typing import Any" in result
    assert "from datetime import date" in result
    assert "import uuid" not in result


# ---------------------------------------------------------------------------
# Multi-database support
# ---------------------------------------------------------------------------


def test_scaffold_generates_single_session_without_databases():
    files = ScaffoldGenerator().generate(KilnConfig())
    paths = {f.path for f in files}
    assert "db/session.py" in paths
    assert not any("primary" in p for p in paths)


def test_scaffold_generates_per_db_sessions():
    cfg = KilnConfig(
        databases=[
            DatabaseConfig(key="primary", url_env="DATABASE_URL", default=True),
            DatabaseConfig(
                key="analytics",
                url_env="ANALYTICS_DATABASE_URL",
                pool_size=2,
            ),
        ]
    )
    files = ScaffoldGenerator().generate(cfg)
    paths = {f.path for f in files}
    assert "db/primary_session.py" in paths
    assert "db/analytics_session.py" in paths
    assert "db/session.py" not in paths


def test_scaffold_per_db_session_uses_correct_env_var():
    cfg = KilnConfig(
        databases=[
            DatabaseConfig(
                key="analytics", url_env="ANALYTICS_DB_URL", default=True
            )
        ]
    )
    files = {f.path: f for f in ScaffoldGenerator().generate(cfg)}
    content = files["db/analytics_session.py"].content
    assert "ANALYTICS_DB_URL" in content
    assert "get_analytics_db" in content


def test_crud_route_uses_default_db_session(full_config):
    files = CRUDGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "db.session" in route.content
    assert "get_db" in route.content


def test_crud_route_uses_named_db_session():
    db_primary = DatabaseConfig(
        key="primary", url_env="DATABASE_URL", default=True
    )
    db_analytics = DatabaseConfig(
        key="analytics", url_env="ANALYTICS_DATABASE_URL"
    )
    model = ModelConfig(
        name="Report",
        table="reports",
        fields=[FieldConfig(name="id", type="uuid", primary_key=True)],
    )
    cfg = KilnConfig(
        module="myapp",
        databases=[db_primary, db_analytics],
        models=[model],
        routes=[
            CRUDRouteConfig(
                model="Report", crud=CrudConfig(), db_key="analytics"
            )
        ],
    )
    files = CRUDGenerator().generate(cfg)
    route = next(f for f in files if "routes/report.py" in f.path)
    assert "db.analytics_session" in route.content
    assert "get_analytics_db" in route.content


def test_view_route_uses_named_db_session():
    db = DatabaseConfig(key="primary", url_env="DATABASE_URL", default=True)
    view = ViewConfig(
        name="active_users",
        parameters=[],
        returns=[ViewColumn(name="id", type="uuid")],
    )
    cfg = KilnConfig(
        databases=[db],
        views=[view],
        routes=[
            ViewRouteConfig(
                view="active_users",
                require_auth=False,
                db_key="primary",
            )
        ],
    )
    files = ViewGenerator().generate(cfg)
    route = next(f for f in files if "routes/" in f.path)
    assert "db.primary_session" in route.content
    assert "get_primary_db" in route.content


def test_resolve_db_session_no_databases():
    from kiln.generators._helpers import resolve_db_session

    assert resolve_db_session(None, []) == ("db.session", "get_db")


def test_resolve_db_session_default():
    from kiln.generators._helpers import resolve_db_session

    dbs = [
        DatabaseConfig(key="primary", default=True),
        DatabaseConfig(key="analytics"),
    ]
    assert resolve_db_session(None, dbs) == (
        "db.primary_session",
        "get_primary_db",
    )


def test_resolve_db_session_by_key():
    from kiln.generators._helpers import resolve_db_session

    dbs = [
        DatabaseConfig(key="primary", default=True),
        DatabaseConfig(key="analytics"),
    ]
    assert resolve_db_session("analytics", dbs) == (
        "db.analytics_session",
        "get_analytics_db",
    )


def test_resolve_db_session_missing_default_raises():
    from kiln.generators._helpers import resolve_db_session

    dbs = [DatabaseConfig(key="primary"), DatabaseConfig(key="analytics")]
    with pytest.raises(ValueError, match="default=True"):
        resolve_db_session(None, dbs)


def test_resolve_db_session_unknown_key_raises():
    from kiln.generators._helpers import resolve_db_session

    dbs = [DatabaseConfig(key="primary", default=True)]
    with pytest.raises(ValueError, match="'nope'"):
        resolve_db_session("nope", dbs)


# ---------------------------------------------------------------------------
# ProjectRouterGenerator
# ---------------------------------------------------------------------------


def _make_app_ref(module: str, prefix: str) -> AppRef:
    return AppRef(config=KilnConfig(module=module), prefix=prefix)


def test_project_router_can_generate():
    cfg = KilnConfig(
        apps=[
            _make_app_ref("blog", "/blog"),
            _make_app_ref("inventory", "/inventory"),
        ]
    )
    assert ProjectRouterGenerator().can_generate(cfg)


def test_project_router_cannot_generate_without_apps():
    assert not ProjectRouterGenerator().can_generate(KilnConfig())


def test_project_router_output_path():
    cfg = KilnConfig(apps=[_make_app_ref("blog", "/blog")])
    (f,) = ProjectRouterGenerator().generate(cfg)
    assert f.path == "routes/__init__.py"


def test_project_router_valid_python():
    cfg = KilnConfig(
        apps=[
            _make_app_ref("blog", "/blog"),
            _make_app_ref("inventory", "/inventory"),
        ]
    )
    (f,) = ProjectRouterGenerator().generate(cfg)
    ast.parse(f.content)


def test_project_router_mounts_all_apps():
    cfg = KilnConfig(
        apps=[
            _make_app_ref("blog", "/blog"),
            _make_app_ref("inventory", "/inventory"),
        ]
    )
    (f,) = ProjectRouterGenerator().generate(cfg)
    assert "blog_router" in f.content
    assert 'prefix="/blog"' in f.content
    assert "inventory_router" in f.content
    assert 'prefix="/inventory"' in f.content


# ---------------------------------------------------------------------------
# Registry — project mode
# ---------------------------------------------------------------------------


def test_registry_project_mode_generates_scaffold_and_apps():
    blog_app = KilnConfig(
        module="blog",
        models=[
            ModelConfig(
                name="Article",
                table="articles",
                fields=[FieldConfig(name="id", type="uuid", primary_key=True)],
            )
        ],
        routes=[CRUDRouteConfig(model="Article", crud=CrudConfig())],
    )
    cfg = KilnConfig(
        auth=AuthConfig(),
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[AppRef(config=blog_app, prefix="/blog")],
    )
    files = GeneratorRegistry.default().run(cfg)
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths
    assert "db/primary_session.py" in paths
    assert "blog/models/article.py" in paths
    assert "blog/routes/article.py" in paths
    assert "routes/__init__.py" in paths


def test_registry_app_mode_generates_scaffold_when_auth_present(full_config):
    files = GeneratorRegistry.default().run(full_config)
    paths = {f.path for f in files}
    # full_config has auth=AuthConfig(), so scaffold should run
    assert "auth/dependencies.py" in paths
    assert "db/base.py" in paths
