"""Tests for foundry output types."""

from foundry import (
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

# -------------------------------------------------------------------
# Field
# -------------------------------------------------------------------


def test_field_defaults():
    f = Field(name="email", py_type="str")
    assert f.name == "email"
    assert f.py_type == "str"
    assert f.optional is False


def test_field_optional():
    f = Field(name="email", py_type="str", optional=True)
    assert f.optional is True


# -------------------------------------------------------------------
# RouteParam
# -------------------------------------------------------------------


def test_route_param_no_default():
    p = RouteParam(name="id", annotation="uuid.UUID")
    assert p.default is None


def test_route_param_with_default():
    p = RouteParam(name="limit", annotation="int", default="20")
    assert p.default == "20"


# -------------------------------------------------------------------
# SchemaClass
# -------------------------------------------------------------------


def test_schema_class_defaults():
    s = SchemaClass(name="UserResource")
    assert s.base == "BaseModel"
    assert s.fields == []
    assert s.validators == []
    assert s.doc is None


def test_schema_class_add_field():
    s = SchemaClass(name="UserResource")
    s.add_field("email", "str")
    s.add_field("age", "int", optional=True)
    assert len(s.fields) == 2
    assert s.fields[0].name == "email"
    assert s.fields[0].optional is False
    assert s.fields[1].optional is True


# -------------------------------------------------------------------
# EnumClass
# -------------------------------------------------------------------


def test_enum_class_defaults():
    e = EnumClass(name="SortField")
    assert e.base == "str, Enum"
    assert e.members == []


def test_enum_class_with_members():
    e = EnumClass(
        name="SortField",
        members=[("NAME", '"name"'), ("PRICE", '"price"')],
    )
    assert len(e.members) == 2


# -------------------------------------------------------------------
# RouteHandler
# -------------------------------------------------------------------


def test_route_handler_defaults():
    h = RouteHandler(
        method="get",
        path="/{id}",
        function_name="get_user",
    )
    assert h.params == []
    assert h.body_param is None
    assert h.response_model is None
    assert h.status_code is None
    assert h.body_lines == []
    assert h.decorators == []


def test_route_handler_add_decorator():
    h = RouteHandler(method="get", path="/", function_name="list_users")
    h.add_decorator("@cache(ttl=60)")
    assert h.decorators == ["@cache(ttl=60)"]


def test_route_handler_prepend_body():
    h = RouteHandler(
        method="get",
        path="/",
        function_name="list_users",
        body_lines=["return result"],
    )
    h.prepend_body("result = db.execute(stmt)")
    assert h.body_lines[0] == "result = db.execute(stmt)"
    assert h.body_lines[1] == "return result"


def test_route_handler_append_body():
    h = RouteHandler(
        method="get",
        path="/",
        function_name="list_users",
        body_lines=["stmt = select(User)"],
    )
    h.append_body("return result")
    assert h.body_lines[-1] == "return result"


# -------------------------------------------------------------------
# RouterMount
# -------------------------------------------------------------------


def test_router_mount_no_prefix():
    m = RouterMount(module="myapp.routes.user", alias="user_router")
    assert m.prefix is None


def test_router_mount_with_prefix():
    m = RouterMount(
        module="blog.routes",
        alias="blog_router",
        prefix="/blog",
    )
    assert m.prefix == "/blog"


# -------------------------------------------------------------------
# SerializerFn
# -------------------------------------------------------------------


def test_serializer_fn():
    s = SerializerFn(
        function_name="to_user_resource",
        model_name="User",
        schema_name="UserResource",
        fields=[
            Field(name="id", py_type="uuid.UUID"),
            Field(name="email", py_type="str"),
        ],
    )
    assert s.function_name == "to_user_resource"
    assert len(s.fields) == 2


# -------------------------------------------------------------------
# TestCase
# -------------------------------------------------------------------


def test_test_case_defaults():
    t = TestCase(
        op_name="get",
        method="get",
        path="/{id}",
        status_success=200,
    )
    assert t.status_not_found is None
    assert t.status_invalid is None
    assert t.requires_auth is False
    assert t.has_request_body is False
    assert t.request_fields == []
    assert t.action_name is None


def test_test_case_full():
    t = TestCase(
        op_name="create",
        method="post",
        path="/",
        status_success=201,
        status_invalid=422,
        requires_auth=True,
        has_request_body=True,
        request_schema="UserCreateRequest",
        request_fields=[{"name": "email", "py_type": "str"}],
    )
    assert t.status_invalid == 422
    assert t.requires_auth is True


# -------------------------------------------------------------------
# StaticFile
# -------------------------------------------------------------------


def test_static_file_defaults():
    f = StaticFile(path="db/session.py", template="init/db_session.py.j2")
    assert f.context == {}


def test_static_file_with_context():
    f = StaticFile(
        path="db/session.py",
        template="init/db_session.py.j2",
        context={"url_env": "DATABASE_URL"},
    )
    assert f.context["url_env"] == "DATABASE_URL"


# -------------------------------------------------------------------
# Mutability
# -------------------------------------------------------------------


def test_output_types_are_mutable():
    h = RouteHandler(method="get", path="/", function_name="list_users")
    h.method = "post"
    assert h.method == "post"

    s = SchemaClass(name="Foo")
    s.name = "Bar"
    assert s.name == "Bar"

    t = TestCase(
        op_name="get",
        method="get",
        path="/",
        status_success=200,
    )
    t.requires_auth = True
    assert t.requires_auth is True
