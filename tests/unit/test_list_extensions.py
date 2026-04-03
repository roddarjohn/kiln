import ast

import pytest

from kiln.config.schema import OperationConfig, ResourceConfig
from kiln.generators._helpers import ImportCollector, Name
from kiln.generators.base import FileSpec
from kiln.generators.fastapi.list_extensions import (
    DEFAULT_OPERATORS,
    ListFilterOperation,
    ListOrderOperation,
    ListPaginateOperation,
)
from kiln.generators.fastapi.operations import SharedContext

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
# ListFilterOperation
# -------------------------------------------------------------------


class TestListFilterOperation:
    def test_adds_filter_schema(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "email", "type": "str"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        assert "UserListFilter" in specs["schema"].exports

    def test_adds_filter_schema_snippet(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "age", "type": "int"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        classes = specs["schema"].context["schema_classes"]
        assert len(classes) == 1
        assert "UserListFilter" in classes[0]

    def test_default_str_operators(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "name", "type": "str"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        snippet = specs["schema"].context["schema_classes"][0]
        for op in DEFAULT_OPERATORS["str"]:
            if op == "eq":
                assert "name:" in snippet
            else:
                assert f"name_{op}:" in snippet

    def test_custom_operators(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[
                {
                    "name": "status",
                    "type": "str",
                    "operators": ["eq", "in"],
                }
            ],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "status:" in snippet
        assert "status_in:" in snippet
        assert "status_contains" not in snippet

    def test_adds_apply_helper_to_route(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "age", "type": "int"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        handlers = specs["route"].context["route_handlers"]
        assert len(handlers) == 1
        assert "apply_user_filters" in handlers[0]

    def test_adds_extra_params(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "age", "type": "int"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        ext = specs["route"].context["list_extensions"]
        assert len(ext["extra_params"]) == 1
        assert "UserListFilter" in ext["extra_params"][0]

    def test_adds_query_modifier(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "age", "type": "int"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        ext = specs["route"].context["list_extensions"]
        assert len(ext["query_modifiers"]) == 1
        assert "apply_user_filters" in ext["query_modifiers"][0]

    def test_adds_sqlalchemy_imports(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "age", "type": "int"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        lines = "\n".join(specs["route"].imports.lines())
        assert "and_" in lines
        assert "Select" in lines

    def test_uuid_field_adds_import(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "ref_id", "type": "uuid"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        lines = "\n".join(specs["schema"].imports.lines())
        assert "uuid" in lines

    def test_bool_field_eq_only(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "active", "type": "bool"}],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "active:" in snippet
        assert "active_gt" not in snippet

    def test_in_operator_uses_list_type(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_filter",
            fields=[
                {
                    "name": "status",
                    "type": "str",
                    "operators": ["in"],
                }
            ],
        )
        opts = ListFilterOperation.Options(**oc.options)
        ListFilterOperation().contribute(specs, resource, shared_ctx, oc, opts)
        snippet = specs["schema"].context["schema_classes"][0]
        assert "list[str]" in snippet


# -------------------------------------------------------------------
# ListOrderOperation
# -------------------------------------------------------------------


class TestListOrderOperation:
    def test_adds_sort_field_enum(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_order",
            fields=[
                {"name": "created_at", "type": "datetime"},
                {"name": "name", "type": "str"},
            ],
            default="created_at",
            default_dir="desc",
        )
        opts = ListOrderOperation.Options(**oc.options)
        ListOrderOperation().contribute(specs, resource, shared_ctx, oc, opts)
        assert "UserSortField" in specs["schema"].exports
        snippet = specs["schema"].context["schema_classes"][0]
        assert "created_at" in snippet
        assert "name" in snippet

    def test_adds_sort_params(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_order",
            fields=[{"name": "created_at", "type": "datetime"}],
            default="created_at",
            default_dir="desc",
        )
        opts = ListOrderOperation.Options(**oc.options)
        ListOrderOperation().contribute(specs, resource, shared_ctx, oc, opts)
        ext = specs["route"].context["list_extensions"]
        params = ext["extra_params"]
        assert len(params) == 2
        assert "sort_by" in params[0]
        assert "sort_dir" in params[1]
        assert '"desc"' in params[1]

    def test_adds_order_by_modifiers(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_order",
            fields=[{"name": "created_at", "type": "datetime"}],
            default="created_at",
        )
        opts = ListOrderOperation.Options(**oc.options)
        ListOrderOperation().contribute(specs, resource, shared_ctx, oc, opts)
        ext = specs["route"].context["list_extensions"]
        modifiers = ext["query_modifiers"]
        combined = "\n".join(modifiers)
        assert "sort_col" in combined
        assert "order_by" in combined
        assert "User.created_at" in combined

    def test_default_dir_asc(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_order",
            fields=[{"name": "name", "type": "str"}],
        )
        opts = ListOrderOperation.Options(**oc.options)
        ListOrderOperation().contribute(specs, resource, shared_ctx, oc, opts)
        ext = specs["route"].context["list_extensions"]
        assert '"asc"' in ext["extra_params"][1]

    def test_default_falls_back_to_pk(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_order",
            fields=[{"name": "name", "type": "str"}],
        )
        opts = ListOrderOperation.Options(**oc.options)
        ListOrderOperation().contribute(specs, resource, shared_ctx, oc, opts)
        ext = specs["route"].context["list_extensions"]
        combined = "\n".join(ext["query_modifiers"])
        assert "User.id" in combined

    def test_adds_enum_import(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_order",
            fields=[{"name": "name", "type": "str"}],
        )
        opts = ListOrderOperation.Options(**oc.options)
        ListOrderOperation().contribute(specs, resource, shared_ctx, oc, opts)
        lines = "\n".join(specs["schema"].imports.lines())
        assert "Enum" in lines

    def test_adds_literal_import(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_order",
            fields=[{"name": "name", "type": "str"}],
        )
        opts = ListOrderOperation.Options(**oc.options)
        ListOrderOperation().contribute(specs, resource, shared_ctx, oc, opts)
        lines = "\n".join(specs["route"].imports.lines())
        assert "Literal" in lines


# -------------------------------------------------------------------
# ListPaginateOperation — keyset mode
# -------------------------------------------------------------------


class TestListPaginateKeyset:
    def test_adds_page_schema(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
            cursor_field="id",
            cursor_type="uuid",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        assert "UserPage" in specs["schema"].exports
        snippet = specs["schema"].context["schema_classes"][0]
        assert "next_cursor" in snippet
        assert "UserResource" in snippet

    def test_overrides_response_model(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        ext = specs["route"].context["list_extensions"]
        assert ext["response_model"] == "UserPage"
        assert ext["return_type"] == "UserPage"

    def test_adds_cursor_params(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
            default_page_size=25,
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        ext = specs["route"].context["list_extensions"]
        params = ext["extra_params"]
        assert any("cursor" in p for p in params)
        assert any("page_size" in p and "25" in p for p in params)

    def test_adds_cursor_query_modifiers(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
            cursor_field="id",
            cursor_type="uuid",
            max_page_size=50,
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        ext = specs["route"].context["list_extensions"]
        combined = "\n".join(ext["query_modifiers"])
        assert "if cursor:" in combined
        assert "uuid.UUID(cursor)" in combined
        assert "limit(page_size + 1)" in combined
        assert "min(page_size, 50)" in combined

    def test_sets_result_expression(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
            cursor_field="id",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        ext = specs["route"].context["list_extensions"]
        expr = ext["result_expression"]
        assert "has_more" in expr
        assert "UserPage(" in expr
        assert "next_cursor" in expr
        assert "to_user_resource" in expr

    def test_int_cursor_type(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
            cursor_field="id",
            cursor_type="int",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        ext = specs["route"].context["list_extensions"]
        combined = "\n".join(ext["query_modifiers"])
        assert "int(cursor)" in combined

    def test_no_resource_schema(self, specs, resource, shared_ctx_no_schema):
        oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx_no_schema, oc, opts
        )
        snippet = specs["schema"].context["schema_classes"][0]
        assert "dict" in snippet
        ext = specs["route"].context["list_extensions"]
        assert "items=items," in ext["result_expression"]


# -------------------------------------------------------------------
# ListPaginateOperation — offset mode
# -------------------------------------------------------------------


class TestListPaginateOffset:
    def test_adds_page_schema_with_total(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="offset",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        assert "UserPage" in specs["schema"].exports
        snippet = specs["schema"].context["schema_classes"][0]
        assert "total" in snippet
        assert "next_cursor" not in snippet

    def test_adds_offset_params(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="offset",
            default_page_size=10,
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        ext = specs["route"].context["list_extensions"]
        params = ext["extra_params"]
        assert any("offset" in p for p in params)
        assert any("limit" in p and "10" in p for p in params)

    def test_offset_result_expression(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="offset",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        ext = specs["route"].context["list_extensions"]
        expr = ext["result_expression"]
        assert "count" in expr
        assert "total" in expr
        assert "UserPage(" in expr

    def test_offset_adds_func_import(self, specs, resource, shared_ctx):
        oc = OperationConfig(
            name="list_paginate",
            mode="offset",
        )
        opts = ListPaginateOperation.Options(**oc.options)
        ListPaginateOperation().contribute(
            specs, resource, shared_ctx, oc, opts
        )
        lines = "\n".join(specs["route"].imports.lines())
        assert "func" in lines


# -------------------------------------------------------------------
# Combined extension tests
# -------------------------------------------------------------------


class TestCombinedExtensions:
    def _contribute_all(self, specs, resource, shared_ctx):
        """Run all three extensions then list."""
        from kiln.generators.fastapi.operations import ListOperation

        # Filter
        filter_oc = OperationConfig(
            name="list_filter",
            fields=[{"name": "email", "type": "str", "operators": ["eq"]}],
        )
        ListFilterOperation().contribute(
            specs,
            resource,
            shared_ctx,
            filter_oc,
            ListFilterOperation.Options(**filter_oc.options),
        )

        # Order
        order_oc = OperationConfig(
            name="list_order",
            fields=[{"name": "created_at", "type": "datetime"}],
            default="created_at",
            default_dir="desc",
        )
        ListOrderOperation().contribute(
            specs,
            resource,
            shared_ctx,
            order_oc,
            ListOrderOperation.Options(**order_oc.options),
        )

        # Paginate
        paginate_oc = OperationConfig(
            name="list_paginate",
            mode="keyset",
            cursor_field="id",
            cursor_type="uuid",
            default_page_size=20,
        )
        ListPaginateOperation().contribute(
            specs,
            resource,
            shared_ctx,
            paginate_oc,
            ListPaginateOperation.Options(**paginate_oc.options),
        )

        # List
        list_oc = OperationConfig(
            name="list",
            fields=[
                {"name": "id", "type": "uuid"},
                {"name": "email", "type": "email"},
            ],
        )
        ListOperation().contribute(
            specs,
            resource,
            shared_ctx,
            list_oc,
            ListOperation.Options(**list_oc.options),
        )

    def test_combined_handler_has_all_params(self, specs, resource, shared_ctx):
        self._contribute_all(specs, resource, shared_ctx)
        handlers = specs["route"].context["route_handlers"]
        # filter apply helper + list handler
        assert len(handlers) == 2
        list_handler = handlers[-1]
        assert "filters:" in list_handler
        assert "sort_by:" in list_handler
        assert "cursor:" in list_handler
        assert "page_size:" in list_handler

    def test_combined_handler_response_model(self, specs, resource, shared_ctx):
        self._contribute_all(specs, resource, shared_ctx)
        handlers = specs["route"].context["route_handlers"]
        list_handler = handlers[-1]
        assert "response_model=UserPage" in list_handler

    def test_combined_handler_has_result_expression(
        self, specs, resource, shared_ctx
    ):
        self._contribute_all(specs, resource, shared_ctx)
        handlers = specs["route"].context["route_handlers"]
        list_handler = handlers[-1]
        assert "UserPage(" in list_handler
        assert "next_cursor" in list_handler

    def test_combined_generates_valid_python(self, specs, resource, shared_ctx):
        self._contribute_all(specs, resource, shared_ctx)
        # Render the route file and check it parses
        route_spec = specs["route"]
        route_spec.imports.add_from("sqlalchemy", "select")
        route_spec.imports.add_from("myapp.models", "User")
        generated = route_spec.render()
        ast.parse(generated.content)

    def test_combined_schema_valid_python(self, specs, resource, shared_ctx):
        self._contribute_all(specs, resource, shared_ctx)
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
        from kiln.generators.fastapi.operations import ListOperation

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
        from kiln.generators.fastapi.operations import ListOperation

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
