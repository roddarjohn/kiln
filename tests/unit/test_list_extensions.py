import ast

import pytest

from kiln.config.schema import FieldSpec, OperationConfig, ResourceConfig
from kiln.generators._helpers import ImportCollector, Name
from kiln.generators.base import FileSpec
from kiln.generators.fastapi.list_extensions import (
    FilterConfig,
    OrderConfig,
    PaginateConfig,
    contribute_filters,
    contribute_ordering,
    contribute_pagination,
    contribute_search_request,
)
from kiln.generators.fastapi.operations import ListOperation, SharedContext

# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

LIST_FIELDS = [
    FieldSpec(name="id", type="uuid"),
    FieldSpec(name="email", type="email"),
    FieldSpec(name="age", type="int"),
    FieldSpec(name="active", type="bool"),
]


@pytest.fixture
def shared_ctx():
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
def shared_ctx_no_schema():
    return SharedContext(
        model=Name("User"),
        model_module="myapp.models",
        pk_name="id",
        pk_py_type="uuid.UUID",
        route_prefix="/users",
        has_auth=False,
        get_db_fn="get_db",
        session_module="db.session",
        has_resource_schema=False,
        response_schema=None,
        package_prefix="",
    )


@pytest.fixture
def resource():
    return ResourceConfig(
        model="myapp.models.User",
        pk="id",
        pk_type="uuid",
        require_auth=False,
    )


@pytest.fixture
def schema_spec():
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
    spec = FileSpec(
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
            "list_extensions": {
                "extra_params": [],
                "query_modifiers": [],
                "response_model": None,
                "return_type": None,
                "result_expression": None,
            },
        },
    )
    spec.imports.add_from("__future__", "annotations")
    spec.imports.add_from("typing", "Annotated")
    spec.imports.add_from("fastapi", "APIRouter", "Depends", "status")
    spec.imports.add_from("sqlalchemy.ext.asyncio", "AsyncSession")
    return spec


@pytest.fixture
def specs(schema_spec, route_spec):
    return {"schema": schema_spec, "route": route_spec}


# -------------------------------------------------------------------
# contribute_filters
# -------------------------------------------------------------------


class TestContributeFilters:
    def test_marks_post_search(self, specs, shared_ctx):
        config = FilterConfig(fields=["email"])
        contribute_filters(specs, shared_ctx, config, LIST_FIELDS)
        ext = specs["route"].context["list_extensions"]
        assert ext["http_method"] == "post"
        assert ext["route_path"] == "/search"

    def test_adds_body_param(self, specs, shared_ctx):
        config = FilterConfig(fields=["email"])
        contribute_filters(specs, shared_ctx, config, LIST_FIELDS)
        ext = specs["route"].context["list_extensions"]
        assert len(ext["extra_params"]) == 1
        assert "UserSearchRequest" in ext["extra_params"][0]

    def test_adds_query_modifier(self, specs, shared_ctx):
        config = FilterConfig(fields=["age"])
        contribute_filters(specs, shared_ctx, config, LIST_FIELDS)
        ext = specs["route"].context["list_extensions"]
        assert len(ext["query_modifiers"]) == 1
        assert "apply_filters" in ext["query_modifiers"][0]

    def test_allowed_fields_in_modifier(self, specs, shared_ctx):
        config = FilterConfig(fields=["email", "age"])
        contribute_filters(specs, shared_ctx, config, LIST_FIELDS)
        ext = specs["route"].context["list_extensions"]
        modifier = ext["query_modifiers"][0]
        assert '"email"' in modifier
        assert '"age"' in modifier

    def test_imports_apply_filters(self, specs, shared_ctx):
        config = FilterConfig(fields=["email"])
        contribute_filters(specs, shared_ctx, config, LIST_FIELDS)
        lines = "\n".join(specs["route"].imports.lines())
        assert "apply_filters" in lines

    def test_auto_derives_fields_from_list_fields(self, specs, shared_ctx):
        config = FilterConfig()
        contribute_filters(specs, shared_ctx, config, LIST_FIELDS)
        ext = specs["route"].context["list_extensions"]
        modifier = ext["query_modifiers"][0]
        for f in LIST_FIELDS:
            assert f'"{f.name}"' in modifier

    def test_empty_allowed_when_no_fields(self, specs, shared_ctx):
        config = FilterConfig()
        contribute_filters(specs, shared_ctx, config, None)
        ext = specs["route"].context["list_extensions"]
        modifier = ext["query_modifiers"][0]
        assert "allowed_fields={}," in modifier


# -------------------------------------------------------------------
# contribute_search_request
# -------------------------------------------------------------------


class TestContributeSearchRequest:
    def test_adds_search_request_export(self, specs, shared_ctx):
        contribute_search_request(specs, shared_ctx, None, None)
        assert "UserSearchRequest" in specs["schema"].exports

    def test_schema_has_filter_field(self, specs, shared_ctx):
        contribute_search_request(specs, shared_ctx, None, None)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "filter:" in snippet

    def test_with_ordering(self, specs, shared_ctx):
        ordering = OrderConfig(
            fields=[FieldSpec(name="name", type="str")],
        )
        contribute_search_request(specs, shared_ctx, ordering, None)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "sort_by:" in snippet
        assert "sort_dir:" in snippet

    def test_with_keyset_pagination(self, specs, shared_ctx):
        pagination = PaginateConfig(mode="keyset", default_page_size=25)
        contribute_search_request(specs, shared_ctx, None, pagination)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "cursor:" in snippet
        assert "page_size:" in snippet
        assert "25" in snippet

    def test_with_offset_pagination(self, specs, shared_ctx):
        pagination = PaginateConfig(mode="offset", default_page_size=10)
        contribute_search_request(specs, shared_ctx, None, pagination)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "offset:" in snippet
        assert "limit:" in snippet
        assert "10" in snippet


# -------------------------------------------------------------------
# contribute_ordering
# -------------------------------------------------------------------


class TestContributeOrdering:
    def test_adds_sort_field_enum(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[
                {"name": "created_at", "type": "datetime"},
                {"name": "name", "type": "str"},
            ],
            default="created_at",
            default_dir="desc",
        )
        contribute_ordering(specs, shared_ctx, config)
        assert "UserSortField" in specs["schema"].exports
        snippet = specs["schema"].context["schema_classes"][0]
        assert "created_at" in snippet
        assert "name" in snippet

    def test_adds_sort_params_no_search_body(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[{"name": "created_at", "type": "datetime"}],
            default="created_at",
            default_dir="desc",
        )
        contribute_ordering(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        params = ext["extra_params"]
        assert len(params) == 2
        assert "sort_by" in params[0]
        assert "sort_dir" in params[1]
        assert '"desc"' in params[1]

    def test_reads_from_body_when_search(self, specs, shared_ctx):
        ext = specs["route"].context["list_extensions"]
        ext["http_method"] = "post"
        config = OrderConfig(
            fields=[{"name": "name", "type": "str"}],
            default="name",
        )
        contribute_ordering(specs, shared_ctx, config)
        modifiers = "\n".join(ext["query_modifiers"])
        assert "body.sort_by" in modifiers
        assert "body.sort_dir" in modifiers
        # No extra query params added
        assert len(ext["extra_params"]) == 0

    def test_adds_order_by_modifiers(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[{"name": "created_at", "type": "datetime"}],
            default="created_at",
        )
        contribute_ordering(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        combined = "\n".join(ext["query_modifiers"])
        assert "sort_col" in combined
        assert "order_by" in combined
        assert "User.created_at" in combined

    def test_default_dir_asc(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[{"name": "name", "type": "str"}],
        )
        contribute_ordering(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        assert '"asc"' in ext["extra_params"][1]

    def test_default_falls_back_to_pk(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[{"name": "name", "type": "str"}],
        )
        contribute_ordering(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        combined = "\n".join(ext["query_modifiers"])
        assert "User.id" in combined

    def test_adds_enum_import(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[{"name": "name", "type": "str"}],
        )
        contribute_ordering(specs, shared_ctx, config)
        lines = "\n".join(specs["schema"].imports.lines())
        assert "Enum" in lines

    def test_adds_literal_import_no_search(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[{"name": "name", "type": "str"}],
        )
        contribute_ordering(specs, shared_ctx, config)
        lines = "\n".join(specs["route"].imports.lines())
        assert "Literal" in lines


# -------------------------------------------------------------------
# contribute_pagination — keyset mode
# -------------------------------------------------------------------


class TestContributePaginationKeyset:
    def test_adds_page_schema(self, specs, shared_ctx):
        config = PaginateConfig(
            mode="keyset",
            cursor_field="id",
            cursor_type="uuid",
        )
        contribute_pagination(specs, shared_ctx, config)
        assert "UserPage" in specs["schema"].exports
        snippet = specs["schema"].context["schema_classes"][0]
        assert "next_cursor" in snippet
        assert "UserResource" in snippet

    def test_overrides_response_model(self, specs, shared_ctx):
        config = PaginateConfig(mode="keyset")
        contribute_pagination(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        assert ext["response_model"] == "UserPage"
        assert ext["return_type"] == "UserPage"

    def test_adds_cursor_params_with_query_validation(self, specs, shared_ctx):
        config = PaginateConfig(
            mode="keyset",
            default_page_size=25,
            max_page_size=100,
        )
        contribute_pagination(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        params = ext["extra_params"]
        assert any("cursor" in p for p in params)
        assert any(
            "page_size" in p and "25" in p and "Query" in p for p in params
        )
        assert any("le=100" in p for p in params)

    def test_reads_from_body_when_search(self, specs, shared_ctx):
        ext = specs["route"].context["list_extensions"]
        ext["http_method"] = "post"
        config = PaginateConfig(mode="keyset")
        contribute_pagination(specs, shared_ctx, config)
        # No query params added
        assert len(ext["extra_params"]) == 0
        modifiers = "\n".join(ext["query_modifiers"])
        assert "body.cursor" in modifiers
        assert "body.page_size" in modifiers

    def test_adds_cursor_query_modifiers(self, specs, shared_ctx):
        config = PaginateConfig(
            mode="keyset",
            cursor_field="id",
            cursor_type="uuid",
        )
        contribute_pagination(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        combined = "\n".join(ext["query_modifiers"])
        assert "if cursor:" in combined
        assert "uuid.UUID(cursor)" in combined
        assert "limit(page_size + 1)" in combined

    def test_sets_result_expression(self, specs, shared_ctx):
        config = PaginateConfig(
            mode="keyset",
            cursor_field="id",
        )
        contribute_pagination(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        expr = ext["result_expression"]
        assert "has_more" in expr
        assert "UserPage(" in expr
        assert "next_cursor" in expr
        assert "to_user_resource" in expr

    def test_int_cursor_type(self, specs, shared_ctx):
        config = PaginateConfig(
            mode="keyset",
            cursor_field="id",
            cursor_type="int",
        )
        contribute_pagination(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        combined = "\n".join(ext["query_modifiers"])
        assert "int(cursor)" in combined

    def test_no_resource_schema(self, specs, shared_ctx_no_schema):
        config = PaginateConfig(mode="keyset")
        contribute_pagination(specs, shared_ctx_no_schema, config)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "dict" in snippet
        ext = specs["route"].context["list_extensions"]
        assert "items=items," in ext["result_expression"]


# -------------------------------------------------------------------
# contribute_pagination — offset mode
# -------------------------------------------------------------------


class TestContributePaginationOffset:
    def test_adds_page_schema_with_total(self, specs, shared_ctx):
        config = PaginateConfig(mode="offset")
        contribute_pagination(specs, shared_ctx, config)
        assert "UserPage" in specs["schema"].exports
        snippet = specs["schema"].context["schema_classes"][0]
        assert "total" in snippet
        assert "next_cursor" not in snippet

    def test_adds_offset_params_with_query_validation(self, specs, shared_ctx):
        config = PaginateConfig(
            mode="offset",
            default_page_size=10,
            max_page_size=50,
        )
        contribute_pagination(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        params = ext["extra_params"]
        assert any("offset" in p and "ge=0" in p for p in params)
        assert any("limit" in p and "10" in p and "le=50" in p for p in params)

    def test_reads_from_body_when_search(self, specs, shared_ctx):
        ext = specs["route"].context["list_extensions"]
        ext["http_method"] = "post"
        config = PaginateConfig(mode="offset")
        contribute_pagination(specs, shared_ctx, config)
        assert len(ext["extra_params"]) == 0
        expr = ext["result_expression"]
        assert "body.offset" in expr
        assert "body.limit" in expr

    def test_offset_result_expression(self, specs, shared_ctx):
        config = PaginateConfig(mode="offset")
        contribute_pagination(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        expr = ext["result_expression"]
        assert "count" in expr
        assert "total" in expr
        assert "UserPage(" in expr

    def test_offset_adds_func_import(self, specs, shared_ctx):
        config = PaginateConfig(mode="offset")
        contribute_pagination(specs, shared_ctx, config)
        lines = "\n".join(specs["route"].imports.lines())
        assert "func" in lines


# -------------------------------------------------------------------
# ListOperation with extensions via Options
# -------------------------------------------------------------------


class TestListOperationWithExtensions:
    def _contribute(self, specs, resource, shared_ctx, **kwargs):
        """Run ListOperation with given options."""
        oc = OperationConfig(name="list", **kwargs)
        opts = ListOperation.Options(**oc.options)
        ListOperation().contribute(specs, resource, shared_ctx, oc, opts)

    def test_combined_handler_post_search(self, specs, resource, shared_ctx):
        self._contribute(
            specs,
            resource,
            shared_ctx,
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
            filters={"fields": ["email"]},
            ordering={
                "fields": [{"name": "created_at", "type": "datetime"}],
                "default": "created_at",
                "default_dir": "desc",
            },
            pagination={
                "mode": "keyset",
                "cursor_field": "id",
                "cursor_type": "uuid",
                "default_page_size": 20,
            },
        )
        handlers = specs["route"].context["route_handlers"]
        assert len(handlers) == 1
        handler = handlers[0]
        assert 'router.post("/search"' in handler
        assert "body:" in handler
        assert "UserSearchRequest" in handler

    def test_combined_handler_response_model(self, specs, resource, shared_ctx):
        self._contribute(
            specs,
            resource,
            shared_ctx,
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
            pagination={"mode": "keyset"},
        )
        handlers = specs["route"].context["route_handlers"]
        assert "response_model=UserPage" in handlers[0]

    def test_combined_generates_valid_python(self, specs, resource, shared_ctx):
        self._contribute(
            specs,
            resource,
            shared_ctx,
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
            filters={"fields": ["email"]},
            ordering={
                "fields": [{"name": "created_at", "type": "datetime"}],
                "default": "created_at",
            },
            pagination={"mode": "keyset"},
        )
        route_spec = specs["route"]
        route_spec.imports.add_from("sqlalchemy", "select")
        route_spec.imports.add_from("myapp.models", "User")
        generated = route_spec.render()
        ast.parse(generated.content)

    def test_combined_schema_valid_python(self, specs, resource, shared_ctx):
        self._contribute(
            specs,
            resource,
            shared_ctx,
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
            filters={"fields": ["email"]},
            ordering={
                "fields": [{"name": "created_at", "type": "datetime"}],
            },
            pagination={"mode": "keyset"},
        )
        schema_spec = specs["schema"]
        schema_spec.imports.add_from("__future__", "annotations")
        schema_spec.imports.add_from("pydantic", "BaseModel")
        generated = schema_spec.render()
        ast.parse(generated.content)

    def test_search_request_includes_sort_and_pagination(
        self, specs, resource, shared_ctx
    ):
        self._contribute(
            specs,
            resource,
            shared_ctx,
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
            filters={},
            ordering={
                "fields": [{"name": "name", "type": "str"}],
            },
            pagination={
                "mode": "keyset",
                "default_page_size": 30,
            },
        )
        assert "UserSearchRequest" in specs["schema"].exports
        classes = specs["schema"].context["schema_classes"]
        search_req = [c for c in classes if "SearchRequest" in c]
        assert len(search_req) == 1
        snippet = search_req[0]
        assert "sort_by:" in snippet
        assert "cursor:" in snippet
        assert "30" in snippet


# -------------------------------------------------------------------
# Backward compatibility
# -------------------------------------------------------------------


class TestBackwardCompat:
    def test_list_without_extensions_unchanged(
        self, specs, resource, shared_ctx
    ):
        oc = OperationConfig(
            name="list",
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
        )
        opts = ListOperation.Options(**oc.options)
        ListOperation().contribute(specs, resource, shared_ctx, oc, opts)
        handlers = specs["route"].context["route_handlers"]
        assert len(handlers) == 1
        handler = handlers[0]
        assert "response_model=list[UserResource]" in handler
        assert "stmt = select(User)" in handler
        assert "to_user_resource" in handler
        # No extension-related content
        assert "cursor" not in handler
        assert "sort_by" not in handler
        assert "body:" not in handler

    def test_list_without_extensions_uses_get(
        self, specs, resource, shared_ctx
    ):
        oc = OperationConfig(
            name="list",
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
        )
        opts = ListOperation.Options(**oc.options)
        ListOperation().contribute(specs, resource, shared_ctx, oc, opts)
        handler = specs["route"].context["route_handlers"][0]
        assert 'router.get("/"' in handler

    def test_list_without_extensions_valid_python(
        self, specs, resource, shared_ctx
    ):
        oc = OperationConfig(
            name="list",
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
        )
        opts = ListOperation.Options(**oc.options)
        ListOperation().contribute(specs, resource, shared_ctx, oc, opts)
        route_spec = specs["route"]
        route_spec.imports.add_from("sqlalchemy", "select")
        route_spec.imports.add_from("myapp.models", "User")
        generated = route_spec.render()
        ast.parse(generated.content)
