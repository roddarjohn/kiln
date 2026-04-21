"""Tests for FastAPI renderers and assembler."""

from unittest.mock import MagicMock

import pytest

from foundry.outputs import (
    EnumClass,
    Field,
    RouteHandler,
    RouteParam,
    RouterMount,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.render import BuildStore, RenderCtx
from kiln.config.schema import AuthConfig, KilnConfig, ResourceConfig
from kiln.generators._env import env as jinja_env
from kiln.renderers.assembler import assemble
from kiln.renderers.fastapi import (
    _build_action_body,
    _render_handler_string,
    _ResourceInfo,
    _response_schema_name,
    _status_suffix,
    create_registry,
    render_enum_class,
    render_router_mount,
    render_schema_class,
    render_serializer,
)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


@pytest.fixture
def registry():
    return create_registry()


@pytest.fixture
def ctx():
    env = MagicMock()
    return RenderCtx(env=env, config={})


# -------------------------------------------------------------------
# _status_suffix
# -------------------------------------------------------------------


def test_status_suffix_known():
    assert _status_suffix(200) == "HTTP_200_OK"
    assert _status_suffix(201) == "HTTP_201_CREATED"
    assert _status_suffix(204) == "HTTP_204_NO_CONTENT"


def test_status_suffix_none():
    assert _status_suffix(None) is None


def test_status_suffix_unknown():
    assert _status_suffix(418) is None


# -------------------------------------------------------------------
# RouteHandler string rendering
# -------------------------------------------------------------------


def test_render_handler_basic():
    handler = RouteHandler(
        method="GET",
        path="/items/{id}",
        function_name="get_item",
        params=[
            RouteParam(name="id", annotation="int"),
        ],
        body_lines=["return await fetch(id)"],
        return_type="Item",
        doc="Get an item by ID.",
    )
    result = _render_handler_string(handler)
    assert '@router.get("/items/{id}")' in result
    assert "async def get_item(" in result
    assert "id: int," in result
    assert ") -> Item:" in result
    assert '    """Get an item by ID."""' in result
    assert "    return await fetch(id)" in result


def test_render_handler_with_status():
    handler = RouteHandler(
        method="POST",
        path="/items",
        function_name="create_item",
        status_code=201,
        body_lines=["pass"],
    )
    result = _render_handler_string(handler)
    assert "status_code=status.HTTP_201_CREATED" in result


def test_render_handler_with_response_model():
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="list_items",
        response_model="list[Item]",
        body_lines=["pass"],
    )
    result = _render_handler_string(handler)
    assert "response_model=list[Item]" in result


def test_render_handler_with_decorators():
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="cached",
        decorators=["@cache(ttl=60)"],
        body_lines=["pass"],
    )
    result = _render_handler_string(handler)
    assert result.startswith("@cache(ttl=60)\n")


def test_render_handler_empty_body():
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="noop",
    )
    result = _render_handler_string(handler)
    assert "    pass" in result


def test_render_handler_default_param():
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="search",
        params=[
            RouteParam(
                name="q",
                annotation="str",
                default='""',
            ),
        ],
        body_lines=["pass"],
    )
    result = _render_handler_string(handler)
    assert '    q: str = "",' in result


# -------------------------------------------------------------------
# Content helpers (standalone, no Fragment wrapper)
# -------------------------------------------------------------------


def test_render_schema_class_string():
    schema = SchemaClass(
        name="UserResource",
        fields=[
            Field(name="id", py_type="int"),
            Field(name="name", py_type="str"),
        ],
    )
    result = render_schema_class(schema)
    assert "class UserResource(BaseModel):" in result
    assert "id: int" in result
    assert "name: str" in result


def test_render_enum_class_string():
    enum = EnumClass(
        name="SortField",
        members=[("NAME", "name"), ("AGE", "age")],
    )
    result = render_enum_class(enum)
    assert "class SortField(str, Enum):" in result
    assert "NAME = 'name'" in result
    assert "AGE = 'age'" in result


def test_render_router_mount_with_prefix():
    mount = RouterMount(
        module="myapp.routes.user",
        alias="user_router",
        prefix="/users",
    )
    result = render_router_mount(mount)
    assert "from myapp.routes.user import router" in result
    assert 'prefix="/users"' in result


def test_render_router_mount_no_prefix():
    mount = RouterMount(
        module="myapp.routes.user",
        alias="user_router",
    )
    result = render_router_mount(mount)
    assert "prefix=" not in result


# -------------------------------------------------------------------
# Registry registrations (only assert shape; Fragment content is
# exercised via the assembler tests and golden output).
# -------------------------------------------------------------------


def test_registry_has_all_types(registry):
    assert registry.has_renderer(SchemaClass)
    assert registry.has_renderer(EnumClass)
    assert registry.has_renderer(RouteHandler)
    assert registry.has_renderer(StaticFile)
    assert registry.has_renderer(TestCase)


def test_registry_static_file_fragment(registry):
    env = MagicMock()
    rctx = RenderCtx(env=env, config={})
    sf = StaticFile(
        path="utils.py",
        template="utils.j2",
        context={"key": "value"},
    )
    fragments = registry.render(sf, rctx)
    assert len(fragments) == 1
    frag = fragments[0]
    assert frag.path == "utils.py"
    assert frag.shell_template == "utils.j2"
    assert frag.shell_context == {"key": "value"}


def test_render_serializer_string():
    from foundry.outputs import SerializerFn

    ser = SerializerFn(
        function_name="to_user_resource",
        model_name="User",
        schema_name="UserResource",
        fields=[Field(name="id", py_type="int")],
    )
    result = render_serializer(ser)
    assert "to_user_resource" in result


# -------------------------------------------------------------------
# Assembler
# -------------------------------------------------------------------


def test_assemble_static_files():
    store = BuildStore()
    store.add(
        "project",
        "project",
        "scaffold",
        StaticFile(
            path="main.py",
            template="main.j2",
            context={"app": "myapp"},
        ),
    )

    tmpl = MagicMock()
    tmpl.render.return_value = "# main\n"
    env = MagicMock()
    env.get_template.return_value = tmpl
    rctx = RenderCtx(env=env, config={})

    reg = create_registry()
    files = assemble(store, reg, rctx)
    assert len(files) == 1
    assert files[0].path == "main.py"
    # Assembler rstrips and appends a single newline.
    assert files[0].content == "# main\n"


def test_assemble_empty_store():
    store = BuildStore()
    env = MagicMock()
    rctx = RenderCtx(env=env, config={})
    reg = create_registry()
    files = assemble(store, reg, rctx)
    assert files == []


def test_assemble_empty_template_static_file():
    """Static files with empty templates produce empty content."""
    store = BuildStore()
    store.add(
        "project",
        "project",
        "scaffold",
        StaticFile(path="pkg/__init__.py", template="", context={}),
    )
    env = MagicMock()
    rctx = RenderCtx(env=env, config={})
    reg = create_registry()
    files = assemble(store, reg, rctx)
    assert len(files) == 1
    assert files[0].path == "pkg/__init__.py"
    assert files[0].content == ""


# -------------------------------------------------------------------
# Registry fragment coverage — resource-scoped renderers
# -------------------------------------------------------------------


def _resource(
    *,
    model: str = "myapp.models.Post",
    pk_type: str = "uuid",
    generate_tests: bool = False,
    route_prefix: str | None = None,
) -> ResourceConfig:
    return ResourceConfig(
        model=model,
        pk_type=pk_type,
        generate_tests=generate_tests,
        route_prefix=route_prefix,
    )


def _rctx(
    resource: ResourceConfig,
    *,
    auth: AuthConfig | None = None,
) -> RenderCtx:
    config = KilnConfig(
        module="myapp",
        auth=auth,
        resources=[resource],
    )
    return RenderCtx(
        env=jinja_env,
        config=config,
        package_prefix="_generated",
        extras={"resource": resource},
    )


def test_enum_fragment(registry):
    enum = EnumClass(
        name="PostSortField",
        members=[("TITLE", "title")],
    )
    fragments = registry.render(enum, _rctx(_resource()))
    assert len(fragments) == 1
    frag = fragments[0]
    assert frag.path == "myapp/schemas/post.py"
    assert (
        "class PostSortField(str, Enum):"
        in frag.shell_context["schema_classes"][0]
    )
    block = frag.imports.block()
    assert "from enum import Enum" in block


def test_serializer_fragment_without_tests(registry):
    ser = SerializerFn(
        function_name="to_post_resource",
        model_name="Post",
        schema_name="PostResource",
        fields=[Field(name="id", py_type="int")],
    )
    fragments = registry.render(ser, _rctx(_resource()))
    assert len(fragments) == 1
    assert fragments[0].path == "myapp/serializers/post.py"


def test_serializer_fragment_with_tests(registry):
    ser = SerializerFn(
        function_name="to_post_resource",
        model_name="Post",
        schema_name="PostResource",
        fields=[Field(name="id", py_type="int")],
    )
    fragments = registry.render(
        ser,
        _rctx(_resource(generate_tests=True)),
    )
    assert len(fragments) == 2
    paths = {f.path for f in fragments}
    assert paths == {
        "myapp/serializers/post.py",
        "tests/test_myapp_post.py",
    }
    test_frag = next(f for f in fragments if f.path.startswith("tests/"))
    assert test_frag.shell_context["has_serializer_test"] is True
    assert test_frag.shell_context["serializer_fields"] == [
        {"name": "id", "py_type": "int"}
    ]


def test_testcase_fragment_skipped_when_tests_disabled(registry):
    tc = TestCase(
        op_name="get",
        method="get",
        path="/{id}",
        status_success=200,
    )
    fragments = registry.render(tc, _rctx(_resource()))
    assert fragments == []


def test_testcase_fragment_no_auth(registry):
    tc = TestCase(
        op_name="list",
        method="get",
        path="/",
        status_success=200,
    )
    fragments = registry.render(
        tc,
        _rctx(_resource(generate_tests=True)),
    )
    frag = fragments[0]
    assert frag.shell_context["has_auth"] is False
    assert frag.shell_context["get_current_user_fn"] is None
    block = frag.imports.block()
    assert "auth.dependencies" not in block


def test_testcase_fragment_with_tests(registry):
    tc = TestCase(
        op_name="get",
        method="get",
        path="/{id}",
        status_success=200,
        status_not_found=404,
    )
    fragments = registry.render(
        tc,
        _rctx(
            _resource(generate_tests=True),
            auth=AuthConfig(verify_credentials_fn="myapp.auth.verify"),
        ),
    )
    assert len(fragments) == 1
    frag = fragments[0]
    assert frag.path == "tests/test_myapp_post.py"
    assert frag.shell_context["model_name"] == "Post"
    assert frag.shell_context["has_auth"] is True
    assert frag.shell_context["get_current_user_fn"] == "get_current_user"
    cases = frag.shell_context["test_cases"]
    assert len(cases) == 1
    assert cases[0]["op_name"] == "get"
    assert cases[0]["status_not_found"] == 404
    block = frag.imports.block()
    assert "from _generated.auth.dependencies" in block


def test_schema_fragment_field_imports(registry):
    schema = SchemaClass(
        name="PostResource",
        fields=[
            Field(name="id", py_type="uuid.UUID"),
            Field(name="created_at", py_type="datetime"),
            Field(name="birthday", py_type="date"),
            Field(name="extra", py_type="dict[str, Any]"),
        ],
    )
    fragments = registry.render(schema, _rctx(_resource()))
    block = fragments[0].imports.block()
    assert "import uuid" in block
    assert "from datetime import date, datetime" in block
    assert "from typing import Any" in block


def test_handler_fragment_datetime_pk(registry):
    h = RouteHandler(
        method="GET",
        path="/{id}",
        function_name="get_post",
        op_name="get",
        response_model="PostResource",
        serializer_fn="to_post_resource",
        return_type="PostResource",
    )
    h.params.append(RouteParam(name="id", annotation="datetime"))
    fragments = registry.render(h, _rctx(_resource(pk_type="datetime")))
    block = fragments[0].imports.block()
    assert "from datetime import datetime" in block
    assert "from _generated.myapp.serializers.post" in block
    assert "from _generated.myapp.schemas.post" in block


def test_handler_fragment_date_pk(registry):
    h = RouteHandler(
        method="GET",
        path="/{id}",
        function_name="get_post",
        op_name="list",
    )
    fragments = registry.render(h, _rctx(_resource(pk_type="date")))
    block = fragments[0].imports.block()
    assert "from datetime import date" in block


def test_handler_fragment_unknown_op_no_db_verb(registry):
    """Handlers not in the CRUD verb map skip the sqlalchemy import."""
    h = RouteHandler(
        method="POST",
        path="/publish",
        function_name="publish_post",
        op_name="custom",
        body_lines=["return None"],
        extra_imports=[("myapp.actions", "publish")],
    )
    fragments = registry.render(h, _rctx(_resource()))
    block = fragments[0].imports.block()
    assert "from sqlalchemy import" not in block
    assert "from myapp.actions import publish" in block


def test_handler_fragment_custom_route_prefix(registry):
    """Custom route_prefix bypasses the ``/{model_lower}s`` fallback."""
    h = RouteHandler(
        method="GET",
        path="/",
        function_name="list_posts",
        op_name="list",
    )
    fragments = registry.render(
        h,
        _rctx(_resource(route_prefix="/articles")),
    )
    assert fragments[0].shell_context["route_prefix"] == "/articles"


# -------------------------------------------------------------------
# _response_schema_name
# -------------------------------------------------------------------


def test_response_schema_name_none():
    h = RouteHandler(method="GET", path="/", function_name="x")
    assert _response_schema_name(h) is None


def test_response_schema_name_plain():
    h = RouteHandler(
        method="GET", path="/", function_name="x", response_model="PostResource"
    )
    assert _response_schema_name(h) == "PostResource"


def test_build_action_body_context():
    from foundry.naming import Name

    info = _ResourceInfo(
        model=Name("Post"),
        model_module="myapp.models",
        app="myapp",
        pkg="_generated",
        route_prefix="/posts",
        pk_name="id",
        pk_py_type="uuid.UUID",
        has_auth=False,
        session_module="db.session",
        get_db_fn="get_db",
        generate_tests=False,
    )
    h = RouteHandler(
        method="POST",
        path="/publish",
        function_name="publish_action",
        op_name="action",
        response_model="PostResource",
        request_schema="PostPublishRequest",
    )
    template, extras = _build_action_body(h, info)
    assert template == "fastapi/ops/action.py.j2"
    assert extras["function_name"] == "publish_action"
    assert extras["method"] == "post"
    assert extras["response_class"] == "PostResource"
    assert extras["request_class"] == "PostPublishRequest"


def test_response_schema_name_list_envelope():
    h = RouteHandler(
        method="GET",
        path="/",
        function_name="x",
        response_model="list[PostListItem]",
    )
    assert _response_schema_name(h) == "PostListItem"


# -------------------------------------------------------------------
# _render_handler_string edge cases
# -------------------------------------------------------------------


def test_render_handler_string_numeric_status_code():
    """Non-mapped status_code is emitted as a numeric literal."""
    h = RouteHandler(
        method="GET",
        path="/",
        function_name="teapot",
        status_code=418,
        body_lines=["pass"],
    )
    result = _render_handler_string(h)
    assert "status_code=418" in result


def test_render_handler_body_unknown_op_falls_through(registry):
    """Unknown op_name falls back to _render_handler_string."""
    h = RouteHandler(
        method="GET",
        path="/",
        function_name="custom",
        op_name="not_a_crud_op",
        body_lines=["return None"],
    )
    fragments = registry.render(h, _rctx(_resource()))
    rendered = fragments[0].shell_context["route_handlers"][0]
    assert "async def custom(" in rendered
    assert "return None" in rendered
