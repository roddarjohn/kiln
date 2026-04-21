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
    StaticFile,
    TestCase,
)
from foundry.render import BuildStore, RenderCtx
from kiln.renderers.assembler import assemble
from kiln.renderers.fastapi import (
    _render_handler_string,
    _status_suffix,
    create_registry,
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
# RouteHandler rendering
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
# Registry renders
# -------------------------------------------------------------------


def test_registry_has_all_types(registry):
    assert registry.has_renderer(SchemaClass)
    assert registry.has_renderer(EnumClass)
    assert registry.has_renderer(RouteHandler)
    assert registry.has_renderer(RouterMount)
    assert registry.has_renderer(StaticFile)
    assert registry.has_renderer(TestCase)


def test_render_schema_class(registry, ctx):
    schema = SchemaClass(
        name="UserResource",
        fields=[
            Field(name="id", py_type="int"),
            Field(name="name", py_type="str"),
        ],
    )
    result = registry.render(schema, ctx)
    assert "class UserResource(BaseModel):" in result
    assert "id: int" in result
    assert "name: str" in result


def test_render_enum_class(registry, ctx):
    enum = EnumClass(
        name="SortField",
        members=[("NAME", "name"), ("AGE", "age")],
    )
    result = registry.render(enum, ctx)
    assert "class SortField(str, Enum):" in result
    assert "NAME = 'name'" in result
    assert "AGE = 'age'" in result


def test_render_route_handler(registry, ctx):
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="root",
        body_lines=["return {}"],
    )
    result = registry.render(handler, ctx)
    assert "async def root(" in result


def test_render_router_mount(registry, ctx):
    mount = RouterMount(
        module="myapp.routes.user",
        alias="user_router",
        prefix="/users",
    )
    result = registry.render(mount, ctx)
    assert "from myapp.routes.user import router" in result
    assert 'prefix="/users"' in result


def test_render_router_mount_no_prefix(registry, ctx):
    mount = RouterMount(
        module="myapp.routes.user",
        alias="user_router",
    )
    result = registry.render(mount, ctx)
    assert "prefix=" not in result


def test_render_static_file(registry):
    tmpl = MagicMock()
    tmpl.render.return_value = "# generated"
    env = MagicMock()
    env.get_template.return_value = tmpl
    ctx = RenderCtx(env=env, config={})

    sf = StaticFile(
        path="utils.py",
        template="utils.j2",
        context={"key": "value"},
    )
    result = registry.render(sf, ctx)
    env.get_template.assert_called_once_with("utils.j2")
    tmpl.render.assert_called_once_with(key="value")
    assert result == "# generated"


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
    ctx = RenderCtx(env=env, config={})

    reg = create_registry()
    files = assemble(store, reg, ctx)
    assert len(files) == 1
    assert files[0].path == "main.py"
    assert files[0].content == "# main\n"


def test_assemble_empty_store():
    store = BuildStore()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})
    reg = create_registry()
    files = assemble(store, reg, ctx)
    assert files == []
