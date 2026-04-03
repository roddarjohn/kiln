"""Tests for kiln code generators."""

import ast
from pathlib import Path

import pytest

from kiln.config.schema import (
    ActionConfig,
    AppRef,
    AuthConfig,
    DatabaseConfig,
    FieldsConfig,
    FieldSpec,
    KilnConfig,
    ResourceConfig,
)
from kiln.generators.base import GeneratedFile, Generator
from kiln.generators.fastapi.project_router import ProjectRouterGenerator
from kiln.generators.fastapi.resource import ResourceGenerator
from kiln.generators.fastapi.router import RouterGenerator
from kiln.generators.fastapi.utils_gen import UtilsGenerator
from kiln.generators.init.scaffold import ScaffoldGenerator
from kiln.generators.registry import GeneratorRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_resource() -> ResourceConfig:
    return ResourceConfig(
        model="myapp.models.User",
        pk="id",
        pk_type="uuid",
        get=True,
        list=FieldsConfig(
            fields=[
                FieldSpec(name="id", type="uuid"),
                FieldSpec(name="email", type="email"),
            ]
        ),
        create=FieldsConfig(
            fields=[
                FieldSpec(name="email", type="email"),
            ]
        ),
        update=FieldsConfig(
            fields=[
                FieldSpec(name="email", type="email"),
            ]
        ),
        delete=True,
        require_auth=["update", "delete"],
    )


@pytest.fixture
def action_resource() -> ResourceConfig:
    return ResourceConfig(
        model="blog.models.Article",
        pk="id",
        pk_type="uuid",
        get=True,
        list=True,
        create=FieldsConfig(fields=[FieldSpec(name="title", type="str")]),
        update=FieldsConfig(fields=[FieldSpec(name="title", type="str")]),
        delete=True,
        require_auth=["create", "update", "delete"],
        actions=[
            ActionConfig(
                name="publish",
                fn="blog.actions.publish_article",
                params=[FieldSpec(name="notify", type="bool")],
                require_auth=True,
            ),
            ActionConfig(
                name="archive",
                fn="blog.actions.archive_article",
                params=[FieldSpec(name="reason", type="str")],
                require_auth=True,
            ),
        ],
    )


@pytest.fixture
def full_config(simple_resource) -> KilnConfig:
    return KilnConfig(
        module="myapp",
        auth=AuthConfig(),
        resources=[simple_resource],
    )


# ---------------------------------------------------------------------------
# GeneratedFile + Generator protocol
# ---------------------------------------------------------------------------


def test_generated_file():
    f = GeneratedFile(path="foo.py", content="# hi")
    assert f.path == "foo.py"
    assert f.content == "# hi"


def test_generator_protocol():
    assert isinstance(ResourceGenerator(), Generator)
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
    assert "auth/dependencies.py" not in paths


def test_scaffold_with_auth_generates_deps():
    cfg = KilnConfig(auth=AuthConfig())
    files = ScaffoldGenerator().generate(cfg)
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths


def test_scaffold_auth_deps_valid_python():
    cfg = KilnConfig(auth=AuthConfig())
    files = {f.path: f for f in ScaffoldGenerator().generate(cfg)}
    src = files["auth/dependencies.py"].content
    ast.parse(src)


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
# ResourceGenerator
# ---------------------------------------------------------------------------


def test_resource_generator_can_generate(full_config):
    assert ResourceGenerator().can_generate(full_config)


def test_resource_generator_cannot_generate_empty():
    assert not ResourceGenerator().can_generate(KilnConfig())


def test_resource_generator_output_paths(full_config):
    files = ResourceGenerator().generate(full_config)
    paths = {f.path for f in files}
    assert "myapp/routes/user.py" in paths
    assert "myapp/schemas/user.py" in paths


def test_resource_generator_valid_python(full_config):
    for f in ResourceGenerator().generate(full_config):
        ast.parse(f.content)


def test_resource_generator_no_build_schema(full_config):
    """get=True generates no schema (no _build_schema, no dynamic class)."""
    files = ResourceGenerator().generate(full_config)
    schema = next(f for f in files if "schemas/user.py" in f.path)
    assert "_build_schema" not in schema.content
    assert "UserGetResponse = _build_schema" not in schema.content
    # get=True → no explicit schema class either
    assert "class UserGetResponse" not in schema.content


def test_resource_generator_specific_fields_static_class(full_config):
    files = ResourceGenerator().generate(full_config)
    schema = next(f for f in files if "schemas/user.py" in f.path)
    # list has specific fields → explicit static Pydantic class
    assert "class UserListResponse(BaseModel):" in schema.content
    assert "id: uuid.UUID" in schema.content


def test_resource_generator_create_request_class(full_config):
    files = ResourceGenerator().generate(full_config)
    schema = next(f for f in files if "schemas/user.py" in f.path)
    assert "class UserCreateRequest(BaseModel):" in schema.content


def test_resource_generator_update_request_optional_fields(full_config):
    files = ResourceGenerator().generate(full_config)
    schema = next(f for f in files if "schemas/user.py" in f.path)
    assert "class UserUpdateRequest(BaseModel):" in schema.content
    # update fields are all optional (| None = None)
    assert "| None = None" in schema.content


def test_resource_generator_route_imports_from_schema(full_config):
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "from _generated.myapp.schemas.user import" in route.content
    # import path uses package_prefix even though file path does not


def test_resource_generator_serializer_functions(full_config):
    """Explicit-field ops get serializer helpers in the route file."""
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    # list=FieldsConfig → _to_user_list serializer
    assert "def _to_user_list(obj: Any) -> UserListResponse:" in route.content
    assert "return UserListResponse(" in route.content
    # get=True → no serializer for get
    assert "def _to_user_get" not in route.content


def test_resource_generator_route_uses_insert(full_config):
    """Create route uses insert() not db.add()."""
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "insert(User)" in route.content
    assert "db.add(" not in route.content


def test_resource_generator_route_uses_update(full_config):
    """Update route uses update() not session ORM methods."""
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "update(User)" in route.content
    assert "db.merge(" not in route.content


def test_resource_generator_route_uses_delete(full_config):
    """Delete route uses delete() not session ORM methods."""
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "delete(User)" in route.content


def test_resource_generator_auth_injected(full_config):
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "current_user" in route.content
    assert "get_current_user" in route.content
    # no uppercase DB alias
    assert "DB = " not in route.content


def test_resource_generator_no_auth_when_unconfigured(simple_resource):
    cfg = KilnConfig(resources=[simple_resource])  # no auth in config
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "get_current_user" not in route.content


def test_resource_generator_delete_route_present(full_config):
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "router.delete" in route.content


def test_resource_generator_no_delete_when_disabled():
    r = ResourceConfig(
        model="myapp.models.User",
        get=True,
        list=True,
        delete=False,
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "router.delete" not in route.content


def test_resource_generator_route_prefix_default():
    r = ResourceConfig(model="myapp.models.Article", get=True)
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/article.py" in f.path)
    assert 'prefix="/articles"' in route.content


def test_resource_generator_route_prefix_custom():
    r = ResourceConfig(
        model="myapp.models.User",
        route_prefix="/people",
        get=True,
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert 'prefix="/people"' in route.content


def test_resource_generator_python_action(action_resource):
    cfg = KilnConfig(resources=[action_resource])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/article.py" in f.path)
    schema = next(f for f in files if "schemas/article.py" in f.path)
    assert "publish_action" in route.content
    # top-level import, not deferred
    assert "from blog.actions import" in route.content
    assert "publish_article" in route.content
    # no deferred import inside the function
    assert "# noqa: PLC0415" not in route.content
    # request class lives in the schema file
    assert "class PublishRequest(BaseModel):" in schema.content


def test_resource_generator_archive_action(action_resource):
    cfg = KilnConfig(resources=[action_resource])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/article.py" in f.path)
    assert "archive_action" in route.content
    # both actions from the same module → one import line
    assert "from blog.actions import" in route.content


def test_resource_generator_action_response(action_resource):
    cfg = KilnConfig(resources=[action_resource])
    files = ResourceGenerator().generate(cfg)
    schema = next(f for f in files if "schemas/article.py" in f.path)
    route = next(f for f in files if "routes/article.py" in f.path)
    assert "class ActionResponse(BaseModel):" in schema.content
    assert "response_model=ActionResponse" in route.content
    assert "return ActionResponse()" in route.content


def test_resource_generator_valid_python_with_actions(action_resource):
    cfg = KilnConfig(resources=[action_resource])
    for f in ResourceGenerator().generate(cfg):
        ast.parse(f.content)


def test_resource_generator_int_pk():
    r = ResourceConfig(
        model="blog.models.Tag",
        pk="id",
        pk_type="int",
        get=True,
        list=True,
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/tag.py" in f.path)
    assert "id: int" in route.content


def test_resource_generator_specific_select_columns():
    """list with specific fields uses per-column select, not select(Model)."""
    r = ResourceConfig(
        model="myapp.models.User",
        list=FieldsConfig(
            fields=[
                FieldSpec(name="id", type="uuid"),
                FieldSpec(name="email", type="email"),
            ]
        ),
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "select(User.id, User.email)" in route.content


def test_resource_generator_all_fields_select_model():
    """list=True uses select(Model), not per-column select."""
    r = ResourceConfig(model="myapp.models.User", list=True)
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "select(User)" in route.content


# ---------------------------------------------------------------------------
# RouterGenerator
# ---------------------------------------------------------------------------


def test_router_generator_can_generate(full_config):
    assert RouterGenerator().can_generate(full_config)


def test_router_generator_output_path(full_config):
    files = RouterGenerator().generate(full_config)
    assert any(f.path == "myapp/routes/__init__.py" for f in files)


def test_router_generator_valid_python(full_config):
    (f,) = RouterGenerator().generate(full_config)
    ast.parse(f.content)


def test_router_generator_includes_all_resources(full_config):
    (f,) = RouterGenerator().generate(full_config)
    assert "user_router" in f.content


def test_router_generator_multiple_resources():
    cfg = KilnConfig(
        module="myapp",
        resources=[
            ResourceConfig(model="myapp.models.User", get=True),
            ResourceConfig(model="myapp.models.Article", list=True),
        ],
    )
    (f,) = RouterGenerator().generate(cfg)
    assert "user_router" in f.content
    assert "article_router" in f.content


# ---------------------------------------------------------------------------
# UtilsGenerator
# ---------------------------------------------------------------------------


def test_utils_generator_can_generate(full_config):
    assert UtilsGenerator().can_generate(full_config)


def test_utils_generator_cannot_generate_empty():
    assert not UtilsGenerator().can_generate(KilnConfig())


def test_utils_generator_output_path(full_config):
    (f,) = UtilsGenerator().generate(full_config)
    assert f.path == "myapp/utils.py"


def test_utils_generator_valid_python(full_config):
    (f,) = UtilsGenerator().generate(full_config)
    ast.parse(f.content)


def test_utils_generator_contains_helper(full_config):
    (f,) = UtilsGenerator().generate(full_config)
    assert "get_object_from_query_or_404" in f.content


def test_resource_generator_uses_utils_for_get_with_schema():
    """GET route with has_schema uses get_object_from_query_or_404."""
    r = ResourceConfig(
        model="myapp.models.User",
        get=FieldsConfig(fields=[FieldSpec(name="id", type="uuid")]),
    )
    cfg = KilnConfig(module="myapp", resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if f.path.endswith("routes/user.py"))
    assert "get_object_from_query_or_404" in route.content
    assert (
        "from _generated.myapp.utils import get_object_from_query_or_404"
        in route.content
    )


def test_resource_generator_no_utils_for_get_without_schema():
    """GET route with get=True (no schema) does NOT import utils."""
    r = ResourceConfig(model="myapp.models.User", get=True)
    cfg = KilnConfig(module="myapp", resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if f.path.endswith("routes/user.py"))
    assert "get_object_from_query_or_404" not in route.content


# ---------------------------------------------------------------------------
# GeneratorRegistry
# ---------------------------------------------------------------------------


def test_registry_default_has_builtins():
    r = GeneratorRegistry.default()
    names = set(r._generators)
    assert "resources" in names
    assert "router" in names
    assert "utils" in names


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
    written = _write_files(files, tmp_path)
    assert written > 0
    written2 = _write_files(files, tmp_path)
    assert written2 == written


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


def test_resource_route_uses_default_db_session(full_config):
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if f.path.endswith("routes/user.py"))
    assert "db.session" in route.content
    assert "get_db" in route.content


def test_resource_route_uses_named_db_session():
    db_primary = DatabaseConfig(
        key="primary", url_env="DATABASE_URL", default=True
    )
    db_analytics = DatabaseConfig(
        key="analytics", url_env="ANALYTICS_DATABASE_URL"
    )
    r = ResourceConfig(
        model="myapp.models.Report",
        get=True,
        db_key="analytics",
    )
    cfg = KilnConfig(
        module="myapp",
        databases=[db_primary, db_analytics],
        resources=[r],
    )
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/report.py" in f.path)
    assert "db.analytics_session" in route.content
    assert "get_analytics_db" in route.content


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
        resources=[
            ResourceConfig(
                model="blog.models.Article",
                get=True,
                list=True,
            )
        ],
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
    assert "blog/routes/article.py" in paths
    assert "routes/__init__.py" in paths


def test_registry_app_mode_generates_scaffold_when_auth_present(full_config):
    files = GeneratorRegistry.default().run(full_config)
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths
    assert "db/base.py" in paths
