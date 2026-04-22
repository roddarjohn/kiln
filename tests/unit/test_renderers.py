"""Tests for FastAPI renderers and assembler."""

from unittest.mock import MagicMock

import pytest

from foundry.assembler import assemble
from foundry.env import create_jinja_env
from foundry.imports import ImportCollector
from foundry.outputs import (
    EnumClass,
    Field,
    RouteHandler,
    RouteParam,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.render import FileFragment, RenderCtx, SnippetFragment
from foundry.render import registry as shared_registry
from foundry.scope import discover_scopes
from foundry.store import BuildStore
from kiln.config.schema import AuthConfig, ProjectConfig, ResourceConfig
from kiln.operations.renderers import (
    _render_handler_string,
    _response_schema_name,
    _status_suffix,
    render_enum_class,
    render_schema_class,
    render_serializer,
)
from kiln.target import target as kiln_target

jinja_env = create_jinja_env(kiln_target.template_dir)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


@pytest.fixture
def registry():
    return shared_registry


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
    result = render_schema_class(schema, jinja_env)
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


# -------------------------------------------------------------------
# Registry registrations (only assert shape; Fragment content is
# exercised via the assembler tests).
# -------------------------------------------------------------------


def test_registry_has_all_types(registry):
    assert SchemaClass in registry._entries
    assert EnumClass in registry._entries
    assert RouteHandler in registry._entries
    assert StaticFile in registry._entries
    assert TestCase in registry._entries


def test_registry_static_file_fragment(registry):
    env = MagicMock()
    rctx = RenderCtx(env=env, config={})
    sf = StaticFile(
        path="db/session.py",
        template="init/db_session.py.j2",
        context={"key": "value"},
    )
    fragments = registry.render(sf, rctx)
    assert len(fragments) == 1
    frag = fragments[0]
    assert isinstance(frag, FileFragment)
    assert frag.path == "db/session.py"
    assert frag.template == "init/db_session.py.j2"
    assert frag.context == {"key": "value"}


def test_render_serializer_string():
    from foundry.outputs import SerializerFn

    ser = SerializerFn(
        function_name="to_user_resource",
        model_name="User",
        schema_name="UserResource",
        fields=[Field(name="id", py_type="int")],
    )
    result = render_serializer(ser, jinja_env)
    assert "to_user_resource" in result


# -------------------------------------------------------------------
# Assembler
# -------------------------------------------------------------------


def test_assemble_static_files():
    store = BuildStore()
    store.add(
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

    reg = shared_registry
    files = assemble(store, reg, rctx)
    assert len(files) == 1
    assert files[0].path == "main.py"
    # Assembler rstrips and appends a single newline.
    assert files[0].content == "# main\n"


def test_assemble_empty_store():
    store = BuildStore()
    env = MagicMock()
    rctx = RenderCtx(env=env, config={})
    reg = shared_registry
    files = assemble(store, reg, rctx)
    assert files == []


def test_assemble_empty_template_static_file():
    """Static files with empty templates produce empty content."""
    store = BuildStore()
    store.add(
        "project",
        "scaffold",
        StaticFile(path="pkg/__init__.py", template="", context={}),
    )
    env = MagicMock()
    rctx = RenderCtx(env=env, config={})
    reg = shared_registry
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


# Scope-instance ids used by :func:`_store_with_resource`.  The
# shorthand ``{module, resources, ...}`` config is wrapped into a
# single implicit app by :class:`ProjectConfig`, so the chain is
# always project → app → resource → operation.
_APP_ID = "project.apps.0"
_RESOURCE_ID = f"{_APP_ID}.resources.0"
_OP_ID = f"{_RESOURCE_ID}.operations.0"


def _rctx(
    resource: ResourceConfig,
    *,
    auth: AuthConfig | None = None,
) -> RenderCtx:
    config = ProjectConfig.model_validate(
        {
            "module": "myapp",
            "resources": [resource.model_dump()],
            "databases": [{"key": "primary", "default": True}],
            **({"auth": auth.model_dump()} if auth is not None else {}),
        }
    )
    return RenderCtx(
        env=jinja_env,
        config=config,
        package_prefix="_generated",
        store=_store_with_resource(resource, config),
        instance_id=_OP_ID,
    )


def _store_with_resource(
    resource: ResourceConfig, config: ProjectConfig
) -> BuildStore:
    """Register a project→app→resource→operation chain for renderer tests.

    Renderers look up the enclosing resource via
    ``ctx.store.ancestor_of(ctx.instance_id, "resource")``, so the
    store must carry that ancestry.  A dummy operation instance
    gives renderers a concrete ``instance_id`` to dispatch on.
    """
    store = BuildStore(scope_tree=discover_scopes(ProjectConfig))
    store.register_instance("project", config)
    store.register_instance(_APP_ID, object(), parent="project")
    store.register_instance(_RESOURCE_ID, resource, parent=_APP_ID)
    store.register_instance(_OP_ID, object(), parent=_RESOURCE_ID)
    return store


def _file(fragments, path):
    """Return the FileFragment at *path* (asserts exactly one)."""
    files = [
        f for f in fragments if isinstance(f, FileFragment) and f.path == path
    ]
    assert len(files) == 1, f"expected one FileFragment at {path}, got {files}"
    return files[0]


def _snippets(fragments, path, slot):
    """Return SnippetFragments at *path* in *slot*, in yield order."""
    return [
        f
        for f in fragments
        if isinstance(f, SnippetFragment) and f.path == path and f.slot == slot
    ]


def test_enum_fragment(registry):
    enum = EnumClass(
        name="PostSortField",
        members=[("TITLE", "title")],
    )
    fragments = registry.render(enum, _rctx(_resource()))
    path = "myapp/schemas/post.py"
    shell = _file(fragments, path)
    assert shell.template == "fastapi/schema_outer.py.j2"
    snippets = _snippets(fragments, path, "schema_classes")
    assert len(snippets) == 1
    assert "class PostSortField(str, Enum):" in snippets[0].value
    assert "from enum import Enum" in snippets[0].imports.block()


def test_serializer_fragment_without_tests(registry):
    ser = SerializerFn(
        function_name="to_post_resource",
        model_name="Post",
        schema_name="PostResource",
        fields=[Field(name="id", py_type="int")],
    )
    fragments = registry.render(ser, _rctx(_resource()))
    paths = {f.path for f in fragments}
    assert paths == {"myapp/serializers/post.py"}


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
    paths = {f.path for f in fragments}
    assert paths == {
        "myapp/serializers/post.py",
        "tests/test_myapp_post.py",
    }
    test_shell = _file(fragments, "tests/test_myapp_post.py")
    assert test_shell.context["has_serializer_test"] is True
    field_snippets = _snippets(
        fragments, "tests/test_myapp_post.py", "serializer_fields"
    )
    assert [s.value for s in field_snippets] == [
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
    shell = _file(fragments, "tests/test_myapp_post.py")
    assert shell.context["has_auth"] is False
    assert shell.context["get_current_user_fn"] is None
    assert "auth.dependencies" not in shell.imports.block()


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
    path = "tests/test_myapp_post.py"
    shell = _file(fragments, path)
    assert shell.context["model_name"] == "Post"
    assert shell.context["has_auth"] is True
    assert shell.context["get_current_user_fn"] == "get_current_user"
    cases = [s.value for s in _snippets(fragments, path, "test_cases")]
    assert len(cases) == 1
    assert cases[0]["op_name"] == "get"
    assert cases[0]["status_not_found"] == 404
    assert "from _generated.auth.dependencies" in shell.imports.block()


def _unioned_imports(fragments):
    """Union every fragment's ImportCollector for block() assertions."""
    merged = ImportCollector()
    for frag in fragments:
        merged.update(frag.imports)
    return merged


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
    block = _unioned_imports(fragments).block()
    assert "import uuid" in block
    assert "from datetime import date, datetime" in block
    assert "from typing import Any" in block


def test_handler_fragment_datetime_pk(registry):
    handler = RouteHandler(
        method="GET",
        path="/{id}",
        function_name="get_post",
        response_model="PostResource",
        serializer_fn="to_post_resource",
        return_type="PostResource",
    )
    handler.params.append(RouteParam(name="id", annotation="datetime"))
    fragments = registry.render(handler, _rctx(_resource(pk_type="datetime")))
    block = _unioned_imports(fragments).block()
    assert "from datetime import datetime" in block
    assert "from _generated.myapp.serializers.post" in block
    assert "from _generated.myapp.schemas.post" in block


def test_handler_fragment_date_pk(registry):
    handler = RouteHandler(
        method="GET",
        path="/{id}",
        function_name="get_post",
    )
    fragments = registry.render(handler, _rctx(_resource(pk_type="date")))
    block = _unioned_imports(fragments).block()
    assert "from datetime import date" in block


def test_handler_fragment_unknown_op_no_db_verb(registry):
    """Handlers not in the CRUD verb map skip the sqlalchemy import."""
    handler = RouteHandler(
        method="POST",
        path="/publish",
        function_name="publish_post",
        body_lines=["return None"],
        extra_imports=[("myapp.actions", "publish")],
    )
    fragments = registry.render(handler, _rctx(_resource()))
    block = _unioned_imports(fragments).block()
    assert "from sqlalchemy import" not in block
    assert "from myapp.actions import publish" in block


def test_handler_fragment_custom_route_prefix(registry):
    """Custom route_prefix bypasses the ``/{model_lower}s`` fallback."""
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="list_posts",
    )
    fragments = registry.render(
        handler,
        _rctx(_resource(route_prefix="/articles")),
    )
    shell = _file(fragments, "myapp/routes/post.py")
    assert shell.context["route_prefix"] == "/articles"


# -------------------------------------------------------------------
# _response_schema_name
# -------------------------------------------------------------------


def test_response_schema_name_none():
    handler = RouteHandler(method="GET", path="/", function_name="x")
    assert _response_schema_name(handler) is None


def test_response_schema_name_plain():
    handler = RouteHandler(
        method="GET", path="/", function_name="x", response_model="PostResource"
    )
    assert _response_schema_name(handler) == "PostResource"


def test_handler_with_body_template_propagates_context(registry):
    """RouteHandler.body_template propagates to the route_handlers snippet."""
    resource = _resource()
    config = ProjectConfig.model_validate(
        {
            "module": "myapp",
            "resources": [resource.model_dump()],
            "databases": [{"key": "primary", "default": True}],
        }
    )
    rctx = RenderCtx(
        env=MagicMock(),
        config=config,
        package_prefix="_generated",
        store=_store_with_resource(resource, config),
        instance_id=_OP_ID,
    )

    handler = RouteHandler(
        method="POST",
        path="/publish",
        function_name="publish_action",
        response_model="PostResource",
        request_schema="PostPublishRequest",
        body_template="fastapi/ops/action.py.j2",
        body_context={
            "function_name": "publish_action",
            "method": "post",
            "path": "/publish",
            "response_class": "PostResource",
            "request_class": "PostPublishRequest",
        },
    )
    fragments = registry.render(handler, rctx)
    path = "myapp/routes/post.py"
    _file(fragments, path)
    [snippet] = _snippets(fragments, path, "route_handlers")
    assert snippet.template == "fastapi/ops/action.py.j2"
    assert snippet.context["function_name"] == "publish_action"
    assert snippet.context["method"] == "post"
    assert snippet.context["response_class"] == "PostResource"
    assert snippet.context["request_class"] == "PostPublishRequest"


def test_response_schema_name_list_envelope():
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="x",
        response_model="list[PostListItem]",
    )
    assert _response_schema_name(handler) == "PostListItem"


# -------------------------------------------------------------------
# _render_handler_string edge cases
# -------------------------------------------------------------------


def test_render_handler_string_numeric_status_code():
    """Non-mapped status_code is emitted as a numeric literal."""
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="teapot",
        status_code=418,
        body_lines=["pass"],
    )
    result = _render_handler_string(handler)
    assert "status_code=418" in result


def test_render_handler_body_unknown_op_falls_through(registry):
    """Plain RouteHandler falls back to _render_handler_string."""
    handler = RouteHandler(
        method="GET",
        path="/",
        function_name="custom",
        body_lines=["return None"],
    )
    fragments = registry.render(handler, _rctx(_resource()))
    [snippet] = _snippets(fragments, "myapp/routes/post.py", "route_handlers")
    # body_template=None path: snippet.value is the pre-rendered string.
    assert snippet.template is None
    assert "async def custom(" in snippet.value
    assert "return None" in snippet.value
