import ast

import pytest

from kiln.config.schema import OperationConfig, ResourceConfig
from kiln.generators._helpers import ImportCollector, Name
from kiln.generators.base import FileSpec
from kiln.generators.fastapi.list_extensions import (
    DEFAULT_OPERATORS,
    FilterConfig,
    OrderConfig,
    PaginateConfig,
    contribute_filters,
    contribute_ordering,
    contribute_pagination,
)
from kiln.generators.fastapi.operations import ListOperation, SharedContext

# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


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
    def test_adds_filter_schema(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "email", "type": "str"}],
        )
        contribute_filters(specs, shared_ctx, config)
        assert "UserListFilter" in specs["schema"].exports

    def test_adds_filter_schema_snippet(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "age", "type": "int"}],
        )
        contribute_filters(specs, shared_ctx, config)
        classes = specs["schema"].context["schema_classes"]
        assert len(classes) == 1
        assert "UserListFilter" in classes[0]

    def test_default_str_operators(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "name", "type": "str"}],
        )
        contribute_filters(specs, shared_ctx, config)
        snippet = specs["schema"].context["schema_classes"][0]
        for op in DEFAULT_OPERATORS["str"]:
            if op == "eq":
                assert "name:" in snippet
            else:
                assert f"name_{op}:" in snippet

    def test_custom_operators(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[
                {
                    "name": "status",
                    "type": "str",
                    "operators": ["eq", "in"],
                }
            ],
        )
        contribute_filters(specs, shared_ctx, config)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "status:" in snippet
        assert "status_in:" in snippet
        assert "status_contains" not in snippet

    def test_adds_extra_params(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "age", "type": "int"}],
        )
        contribute_filters(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        assert len(ext["extra_params"]) == 1
        assert "UserListFilter" in ext["extra_params"][0]

    def test_adds_query_modifier(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "age", "type": "int"}],
        )
        contribute_filters(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        assert len(ext["query_modifiers"]) == 1
        assert "apply_filters" in ext["query_modifiers"][0]

    def test_imports_apply_filters_from_utils(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "age", "type": "int"}],
        )
        contribute_filters(specs, shared_ctx, config)
        lines = "\n".join(specs["route"].imports.lines())
        assert "apply_filters" in lines

    def test_uuid_field_adds_import(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "ref_id", "type": "uuid"}],
        )
        contribute_filters(specs, shared_ctx, config)
        lines = "\n".join(specs["schema"].imports.lines())
        assert "uuid" in lines

    def test_bool_field_eq_only(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[{"name": "active", "type": "bool"}],
        )
        contribute_filters(specs, shared_ctx, config)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "active:" in snippet
        assert "active_gt" not in snippet

    def test_in_operator_uses_list_type(self, specs, shared_ctx):
        config = FilterConfig(
            fields=[
                {
                    "name": "status",
                    "type": "str",
                    "operators": ["in"],
                }
            ],
        )
        contribute_filters(specs, shared_ctx, config)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "list[str]" in snippet


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

    def test_adds_sort_params(self, specs, shared_ctx):
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

    def test_adds_order_by_modifiers(self, specs, shared_ctx):
        config = OrderConfig(
            fields=[{"name": "created_at", "type": "datetime"}],
            default="created_at",
        )
        contribute_ordering(specs, shared_ctx, config)
        ext = specs["route"].context["list_extensions"]
        modifiers = ext["query_modifiers"]
        combined = "\n".join(modifiers)
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

    def test_adds_literal_import(self, specs, shared_ctx):
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

    def test_combined_handler_has_all_params(self, specs, resource, shared_ctx):
        self._contribute(
            specs,
            resource,
            shared_ctx,
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
            filters={
                "fields": [
                    {"name": "email", "type": "str", "operators": ["eq"]}
                ]
            },
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
        assert "filters:" in handler
        assert "sort_by:" in handler
        assert "cursor:" in handler
        assert "page_size:" in handler

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
            filters={
                "fields": [
                    {"name": "email", "type": "str", "operators": ["eq"]}
                ]
            },
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
            filters={
                "fields": [
                    {"name": "email", "type": "str", "operators": ["eq"]}
                ]
            },
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
        assert "filters" not in handler

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
