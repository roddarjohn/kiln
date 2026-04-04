import ast
from pathlib import Path

import pytest

from kiln.config.schema import (
    AppRef,
    AuthConfig,
    DatabaseConfig,
    KilnConfig,
    OperationConfig,
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
        require_auth=False,
        operations=[
            "get",
            OperationConfig(
                name="list",
                fields=[
                    {"name": "id", "type": "uuid"},
                    {"name": "email", "type": "email"},
                ],
            ),
            OperationConfig(
                name="create",
                fields=[
                    {"name": "email", "type": "email"},
                ],
            ),
            OperationConfig(
                name="update",
                require_auth=True,
                fields=[
                    {"name": "email", "type": "email"},
                ],
            ),
            OperationConfig(name="delete", require_auth=True),
        ],
    )


@pytest.fixture
def action_resource() -> ResourceConfig:
    return ResourceConfig(
        model="tests.unit._action_stubs.StubModel",
        pk="id",
        pk_type="uuid",
        require_auth=False,
        operations=[
            "get",
            "list",
            OperationConfig(
                name="create",
                require_auth=True,
                fields=[{"name": "title", "type": "str"}],
            ),
            OperationConfig(
                name="update",
                require_auth=True,
                fields=[{"name": "title", "type": "str"}],
            ),
            OperationConfig(name="delete", require_auth=True),
            OperationConfig(
                name="publish",
                fn="tests.unit._action_stubs.object_action_with_body",
                require_auth=True,
            ),
            OperationConfig(
                name="archive",
                fn="tests.unit._action_stubs.object_action_no_body",
                require_auth=True,
            ),
        ],
    )


@pytest.fixture
def full_config(simple_resource) -> KilnConfig:
    return KilnConfig(
        module="myapp",
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        ),
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
    assert "db/session.py" in paths
    assert "auth/dependencies.py" not in paths


def test_scaffold_with_auth_generates_deps():
    cfg = KilnConfig(
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        )
    )
    files = ScaffoldGenerator().generate(cfg)
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths


def test_scaffold_auth_deps_valid_python():
    cfg = KilnConfig(
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        )
    )
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


def test_scaffold_with_auth_generates_router():
    cfg = KilnConfig(
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        )
    )
    files = ScaffoldGenerator().generate(cfg)
    paths = {f.path for f in files}
    assert "auth/router.py" in paths


def test_scaffold_auth_router_valid_python():
    cfg = KilnConfig(
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        )
    )
    files = {f.path: f for f in ScaffoldGenerator().generate(cfg)}
    src = files["auth/router.py"].content
    ast.parse(src)


def test_scaffold_auth_router_imports_verify_fn():
    cfg = KilnConfig(
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        )
    )
    files = {f.path: f for f in ScaffoldGenerator().generate(cfg)}
    src = files["auth/router.py"].content
    assert "from myapp.auth import verify" in src


def test_scaffold_auth_router_contains_token_url():
    cfg = KilnConfig(
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
            token_url="/api/login",  # noqa: S106
        )
    )
    files = {f.path: f for f in ScaffoldGenerator().generate(cfg)}
    src = files["auth/router.py"].content
    assert "/api/login" in src


def test_scaffold_custom_auth_no_router():
    cfg = KilnConfig(
        auth=AuthConfig(
            get_current_user_fn="myapp.auth.custom.get_current_user"
        )
    )
    files = ScaffoldGenerator().generate(cfg)
    paths = {f.path for f in files}
    assert "auth/router.py" not in paths


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
    # list with fields -> has_resource_schema -> serializer emitted
    assert "myapp/serializers/user.py" in paths


def test_resource_generator_valid_python(full_config):
    for f in ResourceGenerator().generate(full_config):
        ast.parse(f.content)


def test_resource_generator_no_build_schema(full_config):
    files = ResourceGenerator().generate(full_config)
    schema = next(f for f in files if "schemas/user.py" in f.path)
    assert "_build_schema" not in schema.content
    assert "class UserGetResponse" not in schema.content
    assert "class UserListResponse" not in schema.content


def test_resource_generator_specific_fields_static_class(full_config):
    files = ResourceGenerator().generate(full_config)
    schema = next(f for f in files if "schemas/user.py" in f.path)
    # list has specific fields -> unified Resource schema
    assert "class UserResource(BaseModel):" in schema.content
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


def test_resource_generator_serializer_file(full_config):
    files = ResourceGenerator().generate(full_config)
    serializer = next(f for f in files if "serializers/user.py" in f.path)
    assert "def to_user_resource" in serializer.content
    assert "-> UserResource:" in serializer.content
    assert "return UserResource(" in serializer.content
    # route imports the serializer function, not inlines it
    route = next(f for f in files if "routes/user.py" in f.path)
    assert (
        "from _generated.myapp.serializers.user import"
        " to_user_resource" in route.content
    )
    assert "def _to_user" not in route.content


def test_resource_generator_route_uses_insert(full_config):
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "insert(User)" in route.content
    assert "db.add(" not in route.content


def test_resource_generator_route_uses_update(full_config):
    files = ResourceGenerator().generate(full_config)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "update(User)" in route.content
    assert "db.merge(" not in route.content


def test_resource_generator_route_uses_delete(full_config):
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
        operations=["get", "list"],
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "router.delete" not in route.content


def test_resource_generator_route_prefix_default():
    r = ResourceConfig(
        model="myapp.models.Article",
        operations=["get"],
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/article.py" in f.path)
    assert 'prefix="/articles"' in route.content


def test_resource_generator_route_prefix_custom():
    r = ResourceConfig(
        model="myapp.models.User",
        route_prefix="/people",
        operations=["get"],
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert 'prefix="/people"' in route.content


def test_resource_generator_python_action(action_resource):
    cfg = KilnConfig(resources=[action_resource])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/stubmodel.py" in f.path)
    assert "publish_action" in route.content
    # top-level import of the action function
    assert "object_action_with_body" in route.content
    # introspected response model
    assert "response_model=StubResponse" in route.content
    # object action fetches the instance
    assert "get_object_from_query_or_404" in route.content
    # request body passed as typed object
    assert "body: StubRequest" in route.content


def test_resource_generator_archive_action(action_resource):
    cfg = KilnConfig(resources=[action_resource])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/stubmodel.py" in f.path)
    assert "archive_action" in route.content
    # object action without a body
    assert "object_action_no_body" in route.content
    assert "response_model=StubResponse" in route.content


def test_resource_generator_valid_python_with_actions(action_resource):
    cfg = KilnConfig(resources=[action_resource])
    for f in ResourceGenerator().generate(cfg):
        ast.parse(f.content)


def test_resource_generator_int_pk():
    r = ResourceConfig(
        model="blog.models.Tag",
        pk="id",
        pk_type="int",
        operations=["get", "list"],
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/tag.py" in f.path)
    assert "id: int" in route.content


def test_resource_generator_always_select_model():
    r = ResourceConfig(
        model="myapp.models.User",
        operations=[
            OperationConfig(
                name="list",
                fields=[
                    {"name": "id", "type": "uuid"},
                    {"name": "email", "type": "email"},
                ],
            ),
        ],
    )
    cfg = KilnConfig(resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if "routes/user.py" in f.path)
    assert "select(User)" in route.content
    assert "select(User.id" not in route.content


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
            ResourceConfig(model="myapp.models.User", operations=["get"]),
            ResourceConfig(model="myapp.models.Article", operations=["list"]),
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
    assert f.path == "utils.py"


def test_utils_generator_valid_python(full_config):
    (f,) = UtilsGenerator().generate(full_config)
    ast.parse(f.content)


def test_utils_generator_contains_helper(full_config):
    (f,) = UtilsGenerator().generate(full_config)
    assert "get_object_from_query_or_404" in f.content


def test_resource_generator_uses_utils_for_all_get_routes():
    r = ResourceConfig(model="myapp.models.User", operations=["get"])
    cfg = KilnConfig(module="myapp", resources=[r])
    files = ResourceGenerator().generate(cfg)
    route = next(f for f in files if f.path.endswith("routes/user.py"))
    assert "get_object_from_query_or_404" in route.content
    assert (
        "from _generated.utils import"
        " get_object_from_query_or_404" in route.content
    )


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
            DatabaseConfig(
                key="primary",
                url_env="DATABASE_URL",
                default=True,
            ),
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
                key="analytics",
                url_env="ANALYTICS_DB_URL",
                default=True,
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
        operations=["get"],
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


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------


def test_name_pascal_from_snake():
    from kiln.generators._helpers import Name

    assert Name("publish_article").pascal == "PublishArticle"


def test_name_pascal_already_pascal():
    from kiln.generators._helpers import Name

    assert Name("Article").pascal == "Article"


def test_name_pascal_preserves_multi_word_pascal():
    from kiln.generators._helpers import Name

    assert Name("StockMovement").pascal == "StockMovement"


def test_name_pascal_single_lowercase():
    from kiln.generators._helpers import Name

    assert Name("publish").pascal == "Publish"


def test_name_lower():
    from kiln.generators._helpers import Name

    assert Name("Article").lower == "article"


def test_name_slug():
    from kiln.generators._helpers import Name

    assert Name("publish_article").slug == "publish-article"


def test_name_suffixed():
    from kiln.generators._helpers import Name

    assert Name("Article").suffixed("CreateRequest") == "ArticleCreateRequest"


def test_name_suffixed_from_snake():
    from kiln.generators._helpers import Name

    name = Name("publish_article")
    assert name.suffixed("Request") == "PublishArticleRequest"


def test_name_from_dotted():
    from kiln.generators._helpers import Name

    module, name = Name.from_dotted("myapp.models.Article")
    assert module == "myapp.models"
    assert name.pascal == "Article"
    assert name.lower == "article"


# ---------------------------------------------------------------------------
# ImportCollector
# ---------------------------------------------------------------------------


def test_import_collector_bare():
    from kiln.generators._helpers import ImportCollector

    c = ImportCollector()
    c.add("uuid")
    assert c.lines() == ["import uuid"]


def test_import_collector_from():
    from kiln.generators._helpers import ImportCollector

    c = ImportCollector()
    c.add_from("datetime", "datetime", "date")
    assert c.lines() == ["from datetime import date, datetime"]


def test_import_collector_merges_from():
    from kiln.generators._helpers import ImportCollector

    c = ImportCollector()
    c.add_from("datetime", "datetime")
    c.add_from("datetime", "date")
    assert c.lines() == ["from datetime import date, datetime"]


def test_import_collector_deduplicates():
    from kiln.generators._helpers import ImportCollector

    c = ImportCollector()
    c.add("uuid")
    c.add("uuid")
    c.add_from("datetime", "date")
    c.add_from("datetime", "date")
    assert c.lines() == [
        "import uuid",
        "from datetime import date",
    ]


def test_import_collector_mixed():
    from kiln.generators._helpers import ImportCollector

    c = ImportCollector()
    c.add("uuid")
    c.add_from("datetime", "datetime")
    c.add_from("typing", "Any")
    lines = c.lines()
    assert lines == [
        "import uuid",
        "from datetime import datetime",
        "from typing import Any",
    ]


# ---------------------------------------------------------------------------
# resolve_db_session
# ---------------------------------------------------------------------------


def test_resolve_db_session_no_databases():
    from kiln.generators._helpers import resolve_db_session

    assert resolve_db_session(None, []) == (
        "db.session",
        "get_db",
    )


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

    dbs = [
        DatabaseConfig(key="primary"),
        DatabaseConfig(key="analytics"),
    ]
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
                operations=["get", "list"],
            )
        ],
    )
    cfg = KilnConfig(
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        ),
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[AppRef(config=blog_app, prefix="/blog")],
    )
    files = GeneratorRegistry.default().run(cfg)
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths
    assert "db/primary_session.py" in paths
    assert "blog/routes/article.py" in paths
    assert "routes/__init__.py" in paths


def test_registry_app_mode_generates_scaffold_when_auth_present(
    full_config,
):
    files = GeneratorRegistry.default().run(full_config)
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths


# ---------------------------------------------------------------------------
# FileSpec
# ---------------------------------------------------------------------------


def test_filespec_module_with_prefix():
    from kiln.generators._helpers import ImportCollector
    from kiln.generators.base import FileSpec

    spec = FileSpec(
        path="myapp/schemas/user.py",
        template="fastapi/schema_outer.py.j2",
        imports=ImportCollector(),
        package_prefix="_generated",
        context={"model_name": "User", "schema_classes": []},
    )
    assert spec.module == "_generated.myapp.schemas.user"


def test_filespec_module_without_prefix():
    from kiln.generators._helpers import ImportCollector
    from kiln.generators.base import FileSpec

    spec = FileSpec(
        path="myapp/schemas/user.py",
        template="fastapi/schema_outer.py.j2",
        imports=ImportCollector(),
        package_prefix="",
        context={"model_name": "User", "schema_classes": []},
    )
    assert spec.module == "myapp.schemas.user"


def test_filespec_render_produces_generated_file():
    from kiln.generators._helpers import ImportCollector
    from kiln.generators.base import FileSpec

    spec = FileSpec(
        path="myapp/schemas/user.py",
        template="fastapi/schema_outer.py.j2",
        imports=ImportCollector(),
        package_prefix="",
        context={"model_name": "User", "schema_classes": []},
    )
    spec.imports.add_from("__future__", "annotations")
    spec.imports.add_from("pydantic", "BaseModel")
    result = spec.render()
    assert result.path == "myapp/schemas/user.py"
    assert "from __future__ import annotations" in result.content
    assert "from pydantic import BaseModel" in result.content


def test_filespec_render_empty_imports():
    from kiln.generators._helpers import ImportCollector
    from kiln.generators.base import FileSpec

    spec = FileSpec(
        path="myapp/schemas/user.py",
        template="fastapi/schema_outer.py.j2",
        imports=ImportCollector(),
        package_prefix="",
        context={"model_name": "User", "schema_classes": []},
    )
    result = spec.render()
    assert isinstance(result, GeneratedFile)


def test_filespec_exports_default_empty():
    from kiln.generators._helpers import ImportCollector
    from kiln.generators.base import FileSpec

    spec = FileSpec(
        path="myapp/schemas/user.py",
        template="fastapi/schema_outer.py.j2",
        imports=ImportCollector(),
        package_prefix="",
        context={"model_name": "User", "schema_classes": []},
    )
    assert spec.exports == []


# ---------------------------------------------------------------------------
# Operations — unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def fields_resource() -> ResourceConfig:
    return ResourceConfig(
        model="myapp.models.User",
        pk="id",
        pk_type="uuid",
        require_auth=True,
        operations=[
            OperationConfig(
                name="get",
                fields=[
                    {"name": "id", "type": "uuid"},
                    {"name": "email", "type": "email"},
                    {"name": "created_at", "type": "datetime"},
                ],
            ),
            OperationConfig(
                name="list",
                fields=[
                    {"name": "id", "type": "uuid"},
                    {"name": "email", "type": "email"},
                ],
            ),
            OperationConfig(
                name="create",
                fields=[
                    {"name": "email", "type": "email"},
                    {"name": "data", "type": "json"},
                ],
            ),
            OperationConfig(
                name="update",
                fields=[
                    {"name": "email", "type": "email"},
                    {"name": "birthday", "type": "date"},
                ],
            ),
            "delete",
        ],
    )


@pytest.fixture
def shared_ctx():
    from kiln.generators._helpers import Name
    from kiln.generators.fastapi.operations import SharedContext

    return SharedContext(
        model=Name("User"),
        model_module="myapp.models",
        pk_name="id",
        pk_py_type="uuid.UUID",
        route_prefix="/users",
        has_auth=True,
        get_db_fn="get_db",
        session_module="db.session",
        has_resource_schema=True,
        response_schema="UserResource",
        package_prefix="",
    )


@pytest.fixture
def specs(schema_spec, route_spec):
    return {"schema": schema_spec, "route": route_spec}


@pytest.fixture
def schema_spec():
    from kiln.generators._helpers import ImportCollector
    from kiln.generators.base import FileSpec

    return FileSpec(
        path="myapp/schemas/user.py",
        template="fastapi/schema_outer.py.j2",
        imports=ImportCollector(),
        package_prefix="",
        context={
            "model_name": "User",
            "schema_classes": [],
        },
    )


@pytest.fixture
def route_spec():
    from kiln.generators._helpers import ImportCollector
    from kiln.generators.base import FileSpec

    return FileSpec(
        path="myapp/routes/user.py",
        template="fastapi/route.py.j2",
        imports=ImportCollector(),
        package_prefix="",
        context={
            "model_name": "User",
            "model_lower": "user",
            "route_prefix": "/users",
            "route_handlers": [],
            "utils_module": "utils",
        },
    )


def test_operation_protocol_is_runtime_checkable():
    from kiln.generators.fastapi.operations import (
        GetOperation,
        Operation,
    )

    assert isinstance(GetOperation(), Operation)


def test_get_operation_schema_with_fields(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import GetOperation

    oc = OperationConfig(
        name="get",
        fields=[
            {"name": "id", "type": "uuid"},
            {"name": "email", "type": "email"},
            {"name": "created_at", "type": "datetime"},
        ],
    )
    opts = GetOperation.Options(**oc.options)
    GetOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    schema = specs["schema"]
    assert "UserResource" in schema.exports
    assert len(schema.context["schema_classes"]) == 1
    assert "datetime" in "\n".join(schema.imports.lines())


def test_get_operation_schema_no_fields(specs, shared_ctx):
    from kiln.generators.fastapi.operations import GetOperation

    r = ResourceConfig(model="m.M", operations=["get"])
    oc = OperationConfig(name="get")
    opts = GetOperation.Options(**oc.options)
    GetOperation().contribute(specs, r, shared_ctx, oc, opts)
    assert specs["schema"].exports == []
    assert specs["schema"].context["schema_classes"] == []


def test_get_operation_route(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import GetOperation

    oc = OperationConfig(
        name="get",
        fields=[
            {"name": "id", "type": "uuid"},
            {"name": "email", "type": "email"},
            {"name": "created_at", "type": "datetime"},
        ],
    )
    opts = GetOperation.Options(**oc.options)
    GetOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    handlers = specs["route"].context["route_handlers"]
    assert len(handlers) == 1
    assert "@router.get" in handlers[0]


def test_list_operation_skips_duplicate_resource(
    fields_resource, specs, shared_ctx
):
    from kiln.generators.fastapi.operations import (
        GetOperation,
        ListOperation,
    )

    get_oc = OperationConfig(
        name="get",
        fields=[
            {"name": "id", "type": "uuid"},
            {"name": "email", "type": "email"},
        ],
    )
    list_oc = OperationConfig(
        name="list",
        fields=[
            {"name": "id", "type": "uuid"},
            {"name": "email", "type": "email"},
        ],
    )
    get_opts = GetOperation.Options(**get_oc.options)
    GetOperation().contribute(
        specs, fields_resource, shared_ctx, get_oc, get_opts
    )
    list_opts = ListOperation.Options(**list_oc.options)
    ListOperation().contribute(
        specs, fields_resource, shared_ctx, list_oc, list_opts
    )
    assert specs["schema"].exports.count("UserResource") == 1


def test_list_operation_schema_when_get_has_no_fields(specs, shared_ctx):
    from kiln.generators.fastapi.operations import ListOperation

    r = ResourceConfig(
        model="m.M",
        operations=[
            "get",
            OperationConfig(
                name="list",
                fields=[{"name": "id", "type": "uuid"}],
            ),
        ],
    )
    oc = OperationConfig(
        name="list",
        fields=[{"name": "id", "type": "uuid"}],
    )
    opts = ListOperation.Options(**oc.options)
    ListOperation().contribute(specs, r, shared_ctx, oc, opts)
    assert "UserResource" in specs["schema"].exports


def test_list_operation_route(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import ListOperation

    oc = OperationConfig(
        name="list",
        fields=[
            {"name": "id", "type": "uuid"},
            {"name": "email", "type": "email"},
        ],
    )
    opts = ListOperation.Options(**oc.options)
    ListOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    handlers = specs["route"].context["route_handlers"]
    assert len(handlers) == 1


def test_create_operation_schema(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import CreateOperation

    oc = OperationConfig(
        name="create",
        fields=[
            {"name": "email", "type": "email"},
            {"name": "data", "type": "json"},
        ],
    )
    opts = CreateOperation.Options(**oc.options)
    CreateOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    assert "UserCreateRequest" in specs["schema"].exports
    lines = "\n".join(specs["schema"].imports.lines())
    assert "Any" in lines


def test_create_operation_route(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import CreateOperation

    oc = OperationConfig(
        name="create",
        fields=[
            {"name": "email", "type": "email"},
            {"name": "data", "type": "json"},
        ],
    )
    opts = CreateOperation.Options(**oc.options)
    CreateOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    handler = specs["route"].context["route_handlers"][0]
    assert "@router.post" in handler


def test_update_operation_schema(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import UpdateOperation

    oc = OperationConfig(
        name="update",
        fields=[
            {"name": "email", "type": "email"},
            {"name": "birthday", "type": "date"},
        ],
    )
    opts = UpdateOperation.Options(**oc.options)
    UpdateOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    assert "UserUpdateRequest" in specs["schema"].exports
    lines = "\n".join(specs["schema"].imports.lines())
    assert "date" in lines


def test_update_operation_route(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import UpdateOperation

    oc = OperationConfig(
        name="update",
        fields=[
            {"name": "email", "type": "email"},
            {"name": "birthday", "type": "date"},
        ],
    )
    opts = UpdateOperation.Options(**oc.options)
    UpdateOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    handler = specs["route"].context["route_handlers"][0]
    assert "@router.patch" in handler


def test_delete_operation_no_schema(specs, shared_ctx):
    from kiln.generators.fastapi.operations import DeleteOperation

    r = ResourceConfig(model="m.M", operations=["delete"])
    oc = OperationConfig(name="delete")
    opts = DeleteOperation.Options(**oc.options)
    DeleteOperation().contribute(specs, r, shared_ctx, oc, opts)
    assert specs["schema"].exports == []
    assert specs["schema"].context["schema_classes"] == []


def test_delete_operation_route(fields_resource, specs, shared_ctx):
    from kiln.generators.fastapi.operations import DeleteOperation

    oc = OperationConfig(name="delete")
    opts = DeleteOperation.Options(**oc.options)
    DeleteOperation().contribute(specs, fields_resource, shared_ctx, oc, opts)
    handler = specs["route"].context["route_handlers"][0]
    assert "@router.delete" in handler


def test_action_operation_validate_requires_fn():
    from kiln.generators.fastapi.operations import ActionOperation

    oc = OperationConfig(name="publish")
    with pytest.raises(ValueError, match="fn"):
        ActionOperation.Options(**oc.options)


def test_action_operation_object_action(specs, shared_ctx):
    from kiln.generators.fastapi.operations import ActionOperation

    stub = "tests.unit._action_stubs"
    r = ResourceConfig(
        model=f"{stub}.StubModel",
        operations=[
            OperationConfig(
                name="publish",
                fn=f"{stub}.object_action_with_body",
                require_auth=True,
            ),
        ],
    )
    oc = r.operations[0]
    opts = ActionOperation.Options(**oc.options)
    ActionOperation().contribute(specs, r, shared_ctx, oc, opts)
    handler = specs["route"].context["route_handlers"][0]
    assert "@router.post" in handler
    assert "get_object_from_query_or_404" in handler
    assert "body: StubRequest" in handler
    assert "response_model=StubResponse" in handler
    lines = "\n".join(specs["route"].imports.lines())
    assert "object_action_with_body" in lines
    assert "StubRequest" in lines
    assert "StubResponse" in lines
    # No schema exports — types come from consumer code
    assert "ActionResponse" not in specs["schema"].exports


def test_action_operation_collection_action(specs, shared_ctx):
    from kiln.generators.fastapi.operations import ActionOperation

    stub = "tests.unit._action_stubs"
    r = ResourceConfig(
        model=f"{stub}.StubModel",
        operations=[
            OperationConfig(
                name="cleanup",
                fn=f"{stub}.collection_action_with_body",
            ),
        ],
    )
    oc = r.operations[0]
    opts = ActionOperation.Options(**oc.options)
    ActionOperation().contribute(specs, r, shared_ctx, oc, opts)
    handler = specs["route"].context["route_handlers"][0]
    assert "@router.post" in handler
    assert "get_object_from_query_or_404" not in handler
    assert "body: StubRequest" in handler
    assert "response_model=StubResponse" in handler


def test_action_operation_no_body(specs, shared_ctx):
    from kiln.generators.fastapi.operations import ActionOperation

    stub = "tests.unit._action_stubs"
    r = ResourceConfig(
        model=f"{stub}.StubModel",
        operations=[
            OperationConfig(
                name="ping",
                fn=f"{stub}.object_action_no_body",
            ),
        ],
    )
    oc = r.operations[0]
    opts = ActionOperation.Options(**oc.options)
    ActionOperation().contribute(specs, r, shared_ctx, oc, opts)
    handler = specs["route"].context["route_handlers"][0]
    assert "body:" not in handler
    assert "response_model=StubResponse" in handler


# ---------------------------------------------------------------------------
# OperationConfig.options property
# ---------------------------------------------------------------------------


def test_operation_config_options_collects_extras():
    oc = OperationConfig(
        name="create",
        fields=[{"name": "email", "type": "email"}],
    )
    assert oc.options == {"fields": [{"name": "email", "type": "email"}]}


def test_operation_config_options_empty_for_string_shorthand():
    oc = OperationConfig(name="get")
    assert oc.options == {}


def test_operation_config_options_excludes_known_fields():
    oc = OperationConfig(
        name="update",
        require_auth=True,
        fields=[{"name": "email", "type": "email"}],
    )
    assert "require_auth" not in oc.options
    assert "name" not in oc.options
    assert "fields" in oc.options


# ---------------------------------------------------------------------------
# OperationRegistry
# ---------------------------------------------------------------------------


def test_operation_registry_discover():
    from kiln.generators.fastapi.operations import (
        OperationRegistry,
    )

    registry = OperationRegistry.default()
    op = registry.resolve("get", {})
    assert op.name == "get"


def test_operation_registry_resolve_by_name():
    from kiln.generators.fastapi.operations import (
        GetOperation,
        OperationRegistry,
    )

    registry = OperationRegistry()
    registry.register("get", GetOperation)
    op = registry.resolve("get", {})
    assert isinstance(op, GetOperation)


def test_operation_registry_resolve_with_class_option():
    from kiln.generators.fastapi.operations import (
        GetOperation,
        OperationRegistry,
    )

    registry = OperationRegistry()
    op = registry.resolve(
        "custom",
        {"class": ("kiln.generators.fastapi.operations.GetOperation")},
    )
    assert isinstance(op, GetOperation)


def test_operation_registry_resolve_action_by_fn():
    from kiln.generators.fastapi.operations import (
        ActionOperation,
        OperationRegistry,
    )

    registry = OperationRegistry()
    registry.register("action", ActionOperation)
    op = registry.resolve("publish", {"fn": "blog.actions.publish"})
    assert isinstance(op, ActionOperation)


def test_operation_registry_resolve_unknown_raises():
    from kiln.generators.fastapi.operations import (
        OperationRegistry,
    )

    registry = OperationRegistry()
    with pytest.raises(ValueError, match="Unknown operation"):
        registry.resolve("nonexistent", {})


# ---------------------------------------------------------------------------
# ResourcePipeline — integration tests
# ---------------------------------------------------------------------------


def test_pipeline_build_produces_three_files(simple_resource, full_config):
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    pipeline = ResourcePipeline()
    files = pipeline.build(simple_resource, full_config)
    assert len(files) == 3
    paths = [f.path for f in files]
    assert paths[0] == "myapp/schemas/user.py"
    assert paths[1] == "myapp/serializers/user.py"
    assert paths[2] == "myapp/routes/user.py"


def test_pipeline_build_no_serializer_when_no_fields():
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    r = ResourceConfig(
        model="myapp.models.User",
        operations=["get", "list"],
    )
    cfg = KilnConfig(module="myapp")
    files = ResourcePipeline().build(r, cfg)
    assert len(files) == 2
    paths = [f.path for f in files]
    assert "myapp/schemas/user.py" in paths
    assert "myapp/routes/user.py" in paths


def test_pipeline_output_is_valid_python(simple_resource, full_config):
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    files = ResourcePipeline().build(simple_resource, full_config)
    for f in files:
        ast.parse(f.content)


def test_pipeline_schema_exports_wired_to_route(simple_resource, full_config):
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    files = ResourcePipeline().build(simple_resource, full_config)
    route = next(f for f in files if "routes" in f.path)
    assert "UserCreateRequest" in route.content
    assert "UserUpdateRequest" in route.content


def test_pipeline_serializer_wired_to_route(simple_resource, full_config):
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    files = ResourcePipeline().build(simple_resource, full_config)
    route = next(f for f in files if "routes" in f.path)
    assert "to_user_resource" in route.content


def test_pipeline_with_registry():
    from kiln.generators.fastapi.operations import (
        GetOperation,
        OperationRegistry,
    )
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    registry = OperationRegistry()
    registry.register("get", GetOperation)
    pipeline = ResourcePipeline(registry=registry)
    r = ResourceConfig(model="myapp.models.User", operations=["get"])
    cfg = KilnConfig(module="myapp")
    files = pipeline.build(r, cfg)
    route = next(f for f in files if "routes" in f.path)
    assert "@router.get" in route.content
    assert "@router.post" not in route.content
    assert "@router.delete" not in route.content


def test_pipeline_action_resource_valid_python(action_resource):
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    cfg = KilnConfig(
        module="tests",
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        ),
        resources=[action_resource],
    )
    files = ResourcePipeline().build(action_resource, cfg)
    for f in files:
        ast.parse(f.content)


def test_pipeline_auth_imports_when_auth_configured(
    simple_resource, full_config
):
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    files = ResourcePipeline().build(simple_resource, full_config)
    route = next(f for f in files if "routes" in f.path)
    assert "get_current_user" in route.content


def test_pipeline_no_auth_imports_when_no_auth():
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    r = ResourceConfig(model="myapp.models.User", operations=["get"])
    cfg = KilnConfig(module="myapp")
    files = ResourcePipeline().build(r, cfg)
    route = next(f for f in files if "routes" in f.path)
    assert "get_current_user" not in route.content


def test_pipeline_with_package_prefix(simple_resource):
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    cfg = KilnConfig(
        module="myapp",
        auth=AuthConfig(
            verify_credentials_fn="myapp.auth.verify",
        ),
        package_prefix="_generated",
        resources=[simple_resource],
    )
    files = ResourcePipeline().build(simple_resource, cfg)
    route = next(f for f in files if "routes" in f.path)
    assert "_generated." in route.content


def test_pipeline_int_pk_type():
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    r = ResourceConfig(
        model="myapp.models.Item",
        pk="id",
        pk_type="int",
        operations=["get"],
    )
    cfg = KilnConfig(module="myapp")
    files = ResourcePipeline().build(r, cfg)
    route = next(f for f in files if "routes" in f.path)
    assert "int" in route.content
    assert "import uuid" not in route.content


def test_pipeline_custom_route_prefix():
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    r = ResourceConfig(
        model="myapp.models.Item",
        operations=["get"],
        route_prefix="/custom-items",
    )
    cfg = KilnConfig(module="myapp")
    files = ResourcePipeline().build(r, cfg)
    route = next(f for f in files if "routes" in f.path)
    assert "/custom-items" in route.content


def test_resource_generator_delegates_to_pipeline(full_config):
    gen = ResourceGenerator()
    files = gen.generate(full_config)
    paths = [f.path for f in files]
    assert "myapp/schemas/user.py" in paths
    assert "myapp/routes/user.py" in paths


# ---------------------------------------------------------------------------
# Action introspection
# ---------------------------------------------------------------------------

_STUB = "tests.unit._action_stubs"


def test_introspect_object_action_with_body():
    from kiln.generators.fastapi.operations import (
        introspect_action_fn,
    )

    info = introspect_action_fn(
        f"{_STUB}.object_action_with_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is True
    assert info.model_param_name == "obj"
    assert info.request_class == "StubRequest"
    assert info.request_module == _STUB
    assert info.response_class == "StubResponse"
    assert info.response_module == _STUB


def test_introspect_object_action_no_body():
    from kiln.generators.fastapi.operations import (
        introspect_action_fn,
    )

    info = introspect_action_fn(
        f"{_STUB}.object_action_no_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is True
    assert info.model_param_name == "obj"
    assert info.request_class is None
    assert info.response_class == "StubResponse"


def test_introspect_collection_action():
    from kiln.generators.fastapi.operations import (
        introspect_action_fn,
    )

    info = introspect_action_fn(
        f"{_STUB}.collection_action_with_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is False
    assert info.model_param_name is None
    assert info.request_class == "StubRequest"
    assert info.response_class == "StubResponse"


def test_introspect_collection_no_body():
    from kiln.generators.fastapi.operations import (
        introspect_action_fn,
    )

    info = introspect_action_fn(
        f"{_STUB}.collection_action_no_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is False
    assert info.request_class is None
    assert info.response_class == "StubResponse"


def test_introspect_non_basemodel_return_raises():
    from kiln.generators.fastapi.operations import (
        introspect_action_fn,
    )

    with pytest.raises(TypeError, match="BaseModel"):
        introspect_action_fn(
            f"{_STUB}.action_no_return",
            f"{_STUB}.StubModel",
        )


def test_introspect_no_annotations_raises():
    from kiln.generators.fastapi.operations import (
        introspect_action_fn,
    )

    with pytest.raises(TypeError, match="BaseModel"):
        introspect_action_fn(
            f"{_STUB}.action_no_annotations",
            f"{_STUB}.StubModel",
        )


def test_introspect_bad_module_raises():
    from kiln.generators.fastapi.operations import (
        introspect_action_fn,
    )

    with pytest.raises(ValueError, match="Cannot import"):
        introspect_action_fn(
            "nonexistent.module.fn",
            f"{_STUB}.StubModel",
        )
