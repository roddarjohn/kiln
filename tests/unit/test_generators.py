"""Tests for kiln code generators."""

import ast
from pathlib import Path

import pytest

from kiln.config.schema import (
    AuthConfig,
    CrudConfig,
    FieldConfig,
    KilnConfig,
    ModelConfig,
    ViewColumn,
    ViewModel,
    ViewParam,
)
from kiln.generators.base import GeneratedFile, Generator
from kiln.generators.fastapi.crud import CRUDGenerator
from kiln.generators.fastapi.models import PGCraftModelGenerator
from kiln.generators.fastapi.router import RouterGenerator
from kiln.generators.fastapi.views import ViewGenerator
from kiln.generators.init.scaffold import ScaffoldGenerator
from kiln.generators.registry import GeneratorRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_model() -> ModelConfig:
    return ModelConfig(
        name="User",
        table="users",
        schema="public",
        pgcraft_type="simple",
        fields=[
            FieldConfig(name="id", type="uuid", primary_key=True),
            FieldConfig(name="email", type="email", unique=True),
            FieldConfig(
                name="hashed_password", type="str", exclude_from_api=True
            ),
            FieldConfig(
                name="created_at", type="datetime", auto_now_add=True
            ),
        ],
        crud=CrudConfig(require_auth=["update", "delete"]),
    )


@pytest.fixture()
def parameterised_view() -> ViewModel:
    return ViewModel(
        name="summarize_posts_by_user",
        model="Post",
        description="Count posts per user.",
        schema="public",
        parameters=[
            ViewParam(name="start_date", type="date"),
            ViewParam(name="end_date", type="date"),
        ],
        returns=[
            ViewColumn(name="user_id", type="uuid"),
            ViewColumn(name="post_count", type="int"),
        ],
        require_auth=True,
    )


@pytest.fixture()
def plain_view() -> ViewModel:
    return ViewModel(
        name="active_users",
        model="User",
        schema="public",
        parameters=[],
        returns=[
            ViewColumn(name="id", type="uuid"),
            ViewColumn(name="email", type="str"),
        ],
        require_auth=False,
    )


@pytest.fixture()
def full_config(simple_model, parameterised_view) -> KilnConfig:
    return KilnConfig(
        module="app",
        auth=AuthConfig(),
        models=[simple_model],
        views=[parameterised_view],
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


def test_scaffold_generates_expected_paths():
    files = ScaffoldGenerator().generate()
    paths = {f.path for f in files}
    assert "auth/dependencies.py" in paths
    assert "db/base.py" in paths
    assert "db/session.py" in paths


def test_scaffold_files_no_overwrite():
    for f in ScaffoldGenerator().generate():
        assert f.overwrite is False, f"{f.path} should have overwrite=False"


def test_scaffold_auth_deps_valid_python():
    files = {f.path: f for f in ScaffoldGenerator().generate()}
    src = files["auth/dependencies.py"].content
    ast.parse(src)  # raises SyntaxError if invalid


# ---------------------------------------------------------------------------
# PGCraftModelGenerator
# ---------------------------------------------------------------------------


def test_model_generator_can_generate(full_config):
    assert PGCraftModelGenerator().can_generate(full_config)


def test_model_generator_cannot_generate_empty():
    assert not PGCraftModelGenerator().can_generate(KilnConfig())


def test_model_generator_output_paths(full_config):
    files = PGCraftModelGenerator().generate(full_config)
    assert any(f.path == "db/models/user.py" for f in files)


def test_model_generator_valid_python(full_config):
    for f in PGCraftModelGenerator().generate(full_config):
        ast.parse(f.content)


def test_model_generator_contains_class(full_config, simple_model):
    (f,) = PGCraftModelGenerator().generate(full_config)
    assert f"class {simple_model.name}(Base):" in f.content


def test_model_generator_postgrest_plugin():
    m = ModelConfig(
        name="Item",
        table="items",
        pgcraft_plugins=["postgrest"],
        fields=[FieldConfig(name="id", type="int", primary_key=True)],
    )
    cfg = KilnConfig(models=[m])
    (f,) = PGCraftModelGenerator().generate(cfg)
    assert "PostgRESTPlugin" in f.content


# ---------------------------------------------------------------------------
# ViewGenerator
# ---------------------------------------------------------------------------


def test_view_generator_produces_stub_and_route(
    full_config, parameterised_view
):
    files = ViewGenerator().generate(full_config)
    paths = {f.path for f in files}
    assert f"db/views/{parameterised_view.name}.py" in paths
    assert f"api/views/{parameterised_view.name}.py" in paths


def test_view_stub_no_overwrite(full_config, parameterised_view):
    files = ViewGenerator().generate(full_config)
    stub = next(
        f for f in files if f.path == f"db/views/{parameterised_view.name}.py"
    )
    assert stub.overwrite is False


def test_view_route_overwrite(full_config, parameterised_view):
    files = ViewGenerator().generate(full_config)
    route = next(
        f
        for f in files
        if f.path == f"api/views/{parameterised_view.name}.py"
    )
    assert route.overwrite is True


def test_view_route_valid_python(full_config):
    for f in ViewGenerator().generate(full_config):
        if f.path.startswith("api/views/"):
            ast.parse(f.content)


def test_plain_view_route_uses_table(plain_view):
    cfg = KilnConfig(views=[plain_view])
    files = ViewGenerator().generate(cfg)
    route = next(f for f in files if f.path.startswith("api/views/"))
    assert "active_users_table" in route.content
    assert "table_valued" not in route.content


def test_function_view_route_uses_table_valued(parameterised_view):
    cfg = KilnConfig(views=[parameterised_view])
    files = ViewGenerator().generate(cfg)
    route = next(f for f in files if f.path.startswith("api/views/"))
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


def test_crud_generator_output_path(full_config, simple_model):
    files = CRUDGenerator().generate(full_config)
    assert any(f.path == "api/routes/user.py" for f in files)


def test_crud_generator_valid_python(full_config):
    for f in CRUDGenerator().generate(full_config):
        ast.parse(f.content)


def test_crud_generator_includes_auth(full_config):
    (f,) = CRUDGenerator().generate(full_config)
    # update and delete require auth in fixture
    assert "CurrentUser" in f.content
    assert "get_current_user" in f.content


def test_crud_generator_no_auth_when_unconfigured(simple_model):
    cfg = KilnConfig(models=[simple_model])  # no auth config
    (f,) = CRUDGenerator().generate(cfg)
    assert "get_current_user" not in f.content


def test_crud_generator_schemas_present(full_config, simple_model):
    (f,) = CRUDGenerator().generate(full_config)
    assert f"{simple_model.name}Create" in f.content
    assert f"{simple_model.name}Update" in f.content
    assert f"{simple_model.name}Response" in f.content


def test_crud_generator_excluded_field_not_in_schema(full_config):
    (f,) = CRUDGenerator().generate(full_config)
    # hashed_password is exclude_from_api=True
    assert "hashed_password" not in f.content


# ---------------------------------------------------------------------------
# RouterGenerator
# ---------------------------------------------------------------------------


def test_router_generator_can_generate(full_config):
    assert RouterGenerator().can_generate(full_config)


def test_router_generator_output_path(full_config):
    files = RouterGenerator().generate(full_config)
    assert any(f.path == "api/__init__.py" for f in files)


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

        def can_generate(self, config: KilnConfig) -> bool:
            return True

        def generate(self, config: KilnConfig) -> list[GeneratedFile]:
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
    # view stub should not be overwritten on second run
    written2, skipped2 = _write_files(files, tmp_path)
    assert skipped2 > 0
