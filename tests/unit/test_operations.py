"""Tests for kiln.operations — scaffold, routing, crud, action."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from pydantic import BaseModel

from foundry.engine import BuildContext
from foundry.outputs import (
    EnumClass,
    ExtensionSchema,
    Field,
    RouteHandler,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.scope import PROJECT, Scope, ScopeTree
from foundry.store import BuildStore
from kiln.config.schema import (
    App,
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    FieldSpec,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
)
from kiln.operations._list_config import (
    FilterConfig,
    OrderConfig,
    PaginateConfig,
)
from kiln.operations._shared import _field_dicts
from kiln.operations.create import Create
from kiln.operations.delete import Delete
from kiln.operations.get import Get
from kiln.operations.list import List
from kiln.operations.routing import ProjectRouter, Router
from kiln.operations.scaffold import AuthScaffold, Scaffold
from kiln.operations.update import Update

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


class MinimalConfig(BaseModel):
    """Minimal project config for tests."""

    auth: AuthConfig | None = None
    databases: list[DatabaseConfig] = []
    package_prefix: str = "_generated"
    apps: list[object] = []


APP_SCOPE = Scope(name="app", config_key="apps", parent=PROJECT)
RESOURCE_SCOPE = Scope(
    name="resource",
    config_key="resources",
    parent=APP_SCOPE,
)
OPERATION_SCOPE = Scope(
    name="operation",
    config_key="operations",
    parent=RESOURCE_SCOPE,
)
SCOPE_TREE = ScopeTree([PROJECT, APP_SCOPE, RESOURCE_SCOPE, OPERATION_SCOPE])


def _resource_ctx(
    resource: ResourceConfig,
    *,
    config: MinimalConfig | None = None,
    store: BuildStore | None = None,
) -> BuildContext:
    """Build a BuildContext for a resource-scope operation."""
    cfg = config or MinimalConfig()
    s = store or BuildStore(scope_tree=SCOPE_TREE)
    resource_id = "project.apps.0.resources.0"
    s.register_instance(resource_id, resource)
    return BuildContext(
        config=cfg,
        scope=RESOURCE_SCOPE,
        instance=resource,
        instance_id=resource_id,
        store=s,
    )


def _operation_ctx(
    resource: ResourceConfig,
    op_config: OperationConfig,
    *,
    config: MinimalConfig | None = None,
    store: BuildStore | None = None,
) -> BuildContext:
    """Build a BuildContext for an operation-scope op.

    Registers the resource as the op's enclosing ancestor so ops
    can walk ``ctx.store.ancestor_of(ctx.instance_id, "resource")``
    for the resource config.
    """
    cfg = config or MinimalConfig()
    s = store or BuildStore(scope_tree=SCOPE_TREE)
    resource_id = "project.apps.0.resources.0"
    op_id = f"{resource_id}.operations.0"
    s.register_instance(resource_id, resource)
    s.register_instance(op_id, op_config, parent=resource_id)
    return BuildContext(
        config=cfg,
        scope=OPERATION_SCOPE,
        instance=op_config,
        instance_id=op_id,
        store=s,
    )


def _project_ctx(
    config: MinimalConfig | None = None,
) -> BuildContext:
    """Build a BuildContext for a project operation."""
    cfg = config or MinimalConfig()
    return BuildContext(
        config=cfg,
        scope=PROJECT,
        instance=cfg,
        instance_id="project",
        store=BuildStore(),
    )


class _Empty(BaseModel):
    """Empty options stand-in."""


# -------------------------------------------------------------------
# Scaffold
# -------------------------------------------------------------------


class TestScaffold:
    """Tests for Scaffold operation."""

    def test_named_databases(self):
        """Named databases produce per-key session files."""
        config = MinimalConfig(
            databases=[
                DatabaseConfig(key="primary"),
                DatabaseConfig(key="analytics"),
            ]
        )
        ctx = _project_ctx(config)
        result = list(Scaffold().build(ctx, _Empty()))
        paths = [f.path for f in result]
        assert "db/primary_session.py" in paths
        assert "db/analytics_session.py" in paths
        assert "db/session.py" not in paths

    def test_named_db_context(self):
        """Named db session has correct get_db_fn."""
        config = MinimalConfig(
            databases=[DatabaseConfig(key="main", echo=True)]
        )
        ctx = _project_ctx(config)
        result = list(Scaffold().build(ctx, _Empty()))
        session = next(f for f in result if f.path == "db/main_session.py")
        assert session.context["get_db_fn"] == "get_main_db"
        assert session.context["echo"] is True

    def test_no_auth_files_from_scaffold(self):
        """Scaffold never emits auth files -- that's AuthScaffold's job."""
        config = MinimalConfig(
            auth=AuthConfig(
                verify_credentials_fn="myapp.auth.verify",
            )
        )
        ctx = _project_ctx(config)
        result = list(Scaffold().build(ctx, _Empty()))
        paths = [f.path for f in result]
        assert not any(p.startswith("auth/") for p in paths)


# -------------------------------------------------------------------
# AuthScaffold
# -------------------------------------------------------------------


class TestAuthScaffold:
    """Tests for AuthScaffold operation."""

    def test_when_false_without_auth(self):
        """when() returns False when no auth is configured."""
        ctx = _project_ctx()
        assert AuthScaffold().when(ctx) is False

    def test_when_true_with_auth(self):
        """when() returns True when auth is configured."""
        config = MinimalConfig(
            auth=AuthConfig(
                verify_credentials_fn="myapp.auth.verify",
            )
        )
        ctx = _project_ctx(config)
        assert AuthScaffold().when(ctx) is True

    def test_auth_files(self):
        """Auth config produces auth directory files."""
        config = MinimalConfig(
            auth=AuthConfig(
                verify_credentials_fn="myapp.auth.verify",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        paths = [f.path for f in result]
        assert "auth/__init__.py" in paths
        assert "auth/dependencies.py" in paths
        assert "auth/router.py" in paths

    def test_auth_custom_gcu_skips_router(self):
        """Custom get_current_user_fn skips auth router."""
        config = MinimalConfig(
            auth=AuthConfig(
                get_current_user_fn="myapp.auth.custom.get_user",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        paths = [f.path for f in result]
        assert "auth/dependencies.py" in paths
        assert "auth/router.py" not in paths

    def test_auth_deps_context(self):
        """Auth deps context has correct module/name split."""
        config = MinimalConfig(
            auth=AuthConfig(
                get_current_user_fn="myapp.auth.custom.get_user",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        deps = next(f for f in result if f.path == "auth/dependencies.py")
        assert deps.context["gcu_module"] == "myapp.auth.custom"
        assert deps.context["gcu_name"] == "get_user"


# -------------------------------------------------------------------
# Router
# -------------------------------------------------------------------


#: Scope tree mirroring the production config: ``project → app →
#: resource``.  Used by the routing tests to populate
#: :attr:`BuildStore.scopes` so ``scope_of`` works on dot-path ids.
_ROUTER_APP_SCOPE = Scope(name="app", config_key="apps", parent=PROJECT)
_ROUTER_RESOURCE_SCOPE = Scope(
    name="resource",
    config_key="resources",
    parent=_ROUTER_APP_SCOPE,
)
_ROUTER_SCOPE_TREE = ScopeTree(
    [PROJECT, _ROUTER_APP_SCOPE, _ROUTER_RESOURCE_SCOPE]
)


def _app_id(app_index: int) -> str:
    return f"project.apps.{app_index}"


def _resource_id(app_index: int, resource_index: int) -> str:
    return f"{_app_id(app_index)}.resources.{resource_index}"


class TestRouter:
    """Tests for Router operation."""

    @staticmethod
    def _res(name: str) -> ResourceConfig:
        """A ResourceConfig whose model class is *name*."""
        return ResourceConfig(model=f"pkg.models.{name.capitalize()}")

    @staticmethod
    def _ctx(
        module: str,
        resources: list[ResourceConfig],
        store: BuildStore,
        *,
        app_index: int = 0,
    ) -> BuildContext:
        """Context for Router running at the app scope.

        Registers the app on the store so descendants_of_type can
        walk under it, and returns a :class:`BuildContext` with
        the canonical ``project.apps.<N>`` id.
        """
        app = App(
            config=AppConfig(module=module, resources=resources),
            prefix="",
        )
        project = ProjectConfig(
            apps=[app],
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        app_id = _app_id(app_index)
        store.register_instance(app_id, app)
        return BuildContext(
            config=project,
            scope=_ROUTER_APP_SCOPE,
            instance=app,
            instance_id=app_id,
            store=store,
        )

    @staticmethod
    def _add_resource(
        store: BuildStore,
        app_id: str,
        resource_index: int,
        resource: ResourceConfig,
    ) -> str:
        """Register *resource* under *app_id* and return its instance id."""
        iid = f"{app_id}.resources.{resource_index}"
        store.register_instance(iid, resource, parent=app_id)
        return iid

    @classmethod
    def _add_handler(
        cls,
        store: BuildStore,
        app_id: str,
        resource_index: int,
        resource: ResourceConfig,
        op_name: str = "get",
    ) -> str:
        """Register *resource* under *app_id* and add a RouteHandler."""
        iid = cls._add_resource(store, app_id, resource_index, resource)
        slug = resource.model.rpartition(".")[-1].lower()
        store.add(
            iid,
            op_name,
            RouteHandler(
                method="GET",
                path="/{id}",
                function_name=f"{op_name}_{slug}",
            ),
        )
        return iid

    def test_mounts_resources_from_store(self):
        """One route entry per resource with a RouteHandler in the store."""
        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        post, comment = self._res("Post"), self._res("Comment")
        ctx = self._ctx("blog", [post, comment], store)
        self._add_handler(store, ctx.instance_id, 0, post)
        self._add_handler(store, ctx.instance_id, 1, comment)

        result = list(Router().build(ctx, _Empty()))
        statics = [r for r in result if isinstance(r, StaticFile)]

        assert len(statics) == 1
        assert statics[0].path == "blog/routes/__init__.py"
        routes = statics[0].context["routes"]
        aliases = [r["alias"] for r in routes]
        modules = [r["module_name"] for r in routes]
        assert aliases == ["post_router", "comment_router"]
        assert modules == ["post", "comment"]

    def test_router_static_context(self):
        """Static file context has correct route entries."""
        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        user = self._res("User")
        ctx = self._ctx("api", [user], store)
        self._add_handler(store, ctx.instance_id, 0, user)

        result = list(Router().build(ctx, _Empty()))
        static = next(r for r in result if isinstance(r, StaticFile))
        routes = static.context["routes"]
        assert len(routes) == 1
        assert routes[0]["module_name"] == "user"
        assert routes[0]["alias"] == "user_router"

    def test_deduplicates_iid_across_ops(self):
        """One resource with multiple route-emitting ops mounts once."""
        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        user = self._res("User")
        ctx = self._ctx("api", [user], store)
        iid = self._add_resource(store, ctx.instance_id, 0, user)
        store.add(
            iid,
            "get",
            RouteHandler(method="GET", path="/{id}", function_name="get_user"),
        )
        store.add(
            iid,
            "list",
            RouteHandler(method="GET", path="/", function_name="list_user"),
        )

        result = list(Router().build(ctx, _Empty()))
        static = next(r for r in result if isinstance(r, StaticFile))
        routes = static.context["routes"]
        assert [r["alias"] for r in routes] == ["user_router"]

    def test_skips_resources_without_handlers(self):
        """A resource with no RouteHandler entries is not mounted."""
        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        silent, loud = self._res("Silent"), self._res("Loud")
        ctx = self._ctx("api", [silent, loud], store)
        silent_iid = self._add_resource(store, ctx.instance_id, 0, silent)
        store.add(
            silent_iid,
            "some_op",
            StaticFile(path="silent.py", template="x.j2"),
        )
        self._add_handler(store, ctx.instance_id, 1, loud)

        result = list(Router().build(ctx, _Empty()))
        static = next(r for r in result if isinstance(r, StaticFile))
        routes = static.context["routes"]
        assert [r["alias"] for r in routes] == ["loud_router"]

    def test_ignores_non_resource_scope(self):
        """RouteHandlers outside resource scope are not mounted."""
        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        store.add(
            "project",
            "whatever",
            RouteHandler(method="GET", path="/", function_name="root"),
        )
        ctx = self._ctx("api", [self._res("User")], store)

        result = list(Router().build(ctx, _Empty()))
        assert result == []

    def test_no_handlers_returns_empty(self):
        """Empty store → no output."""
        ctx = self._ctx(
            "app",
            [self._res("User")],
            BuildStore(scope_tree=_ROUTER_SCOPE_TREE),
        )
        result = list(Router().build(ctx, _Empty()))
        assert result == []

    def test_per_app_invocation_emits_its_own_router(self):
        """Each app-scope invocation emits only its own app's router."""
        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        post, product = self._res("Post"), self._res("Product")
        blog_ctx = self._ctx("blog", [post], store, app_index=0)
        shop_ctx = self._ctx("shop", [product], store, app_index=1)
        self._add_handler(store, blog_ctx.instance_id, 0, post)
        self._add_handler(store, shop_ctx.instance_id, 0, product)

        blog_statics = [
            r
            for r in Router().build(blog_ctx, _Empty())
            if isinstance(r, StaticFile)
        ]
        shop_statics = [
            r
            for r in Router().build(shop_ctx, _Empty())
            if isinstance(r, StaticFile)
        ]

        assert [s.path for s in blog_statics] == ["blog/routes/__init__.py"]
        assert [s.path for s in shop_statics] == ["shop/routes/__init__.py"]


# -------------------------------------------------------------------
# ProjectRouter
# -------------------------------------------------------------------


class TestProjectRouter:
    """Tests for ProjectRouter operation."""

    def test_no_apps_returns_empty(self):
        config = MinimalConfig()
        ctx = _project_ctx(config)
        result = list(ProjectRouter().build(ctx, _Empty()))
        assert result == []

    def test_with_apps(self):
        app_config = AppConfig(module="blog")
        config = MinimalConfig(
            apps=[App(config=app_config, prefix="/blog")],
        )
        ctx = _project_ctx(config)
        result = list(ProjectRouter().build(ctx, _Empty()))
        assert len(result) == 1
        sf = result[0]
        assert sf.path == "routes/__init__.py"
        assert sf.context["has_auth"] is False
        assert len(sf.context["apps"]) == 1
        assert sf.context["apps"][0]["alias"] == "blog"
        assert sf.context["apps"][0]["prefix"] == "/blog"

    def test_with_auth_and_apps(self):
        app_config = AppConfig(module="blog")
        config = MinimalConfig(
            auth=AuthConfig(
                verify_credentials_fn="myapp.verify",
            ),
            apps=[App(config=app_config, prefix="/blog")],
        )
        ctx = _project_ctx(config)
        result = list(ProjectRouter().build(ctx, _Empty()))
        sf = result[0]
        assert sf.context["has_auth"] is True
        assert sf.context["auth_module"] == "_generated.auth"


# -------------------------------------------------------------------
# CRUD helpers
# -------------------------------------------------------------------


class TestCrudHelpers:
    """Tests for shared CRUD helper functions."""

    def test_field_dicts(self):
        fields = [
            FieldSpec(name="title", type="str"),
            FieldSpec(name="count", type="int"),
        ]
        result = _field_dicts(fields)
        assert len(result) == 2
        assert result[0] == Field(name="title", py_type="str")
        assert result[1] == Field(name="count", py_type="int")


# -------------------------------------------------------------------
# Get
# -------------------------------------------------------------------

_FIELDS = [
    FieldSpec(name="name", type="str"),
    FieldSpec(name="age", type="int"),
]


class _FieldsOpts(BaseModel):
    fields: list[FieldSpec]


# -------------------------------------------------------------------
# Get
# -------------------------------------------------------------------


class TestGet:
    """Tests for Get operation."""

    def test_get_emits_schema_and_handler(self):
        """Get emits its own ``{Model}Resource`` schema + serializer."""
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert len(schemas) == 1
        assert schemas[0].name == "UserResource"

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert len(sers) == 1
        assert sers[0].function_name == "to_user_resource"
        assert sers[0].schema_name == "UserResource"

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.response_model == "UserResource"
        assert handler.return_type == "UserResource"
        assert handler.serializer_fn == "to_user_resource"

    def test_get_test_case(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))

        tests = [r for r in result if isinstance(r, TestCase)]
        assert len(tests) == 1
        assert tests[0].op_name == "get"
        assert tests[0].status_success == 200
        assert tests[0].status_not_found == 404
        # Auth is an extension-style operation; Get itself never
        # sets requires_auth.  See tests in test_operations_auth.py
        # for the end-to-end behavior.
        assert tests[0].requires_auth is False

    def test_get_pk_param(self):
        resource = ResourceConfig(
            model="app.models.User",
            pk="user_id",
            pk_type="int",
        )
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.path == "/{user_id}"
        assert handler.params[0].name == "user_id"
        assert handler.params[0].annotation == "int"


# -------------------------------------------------------------------
# List
# -------------------------------------------------------------------


class TestList:
    """Tests for List operation."""

    def test_list_emits_schema_and_handler(self):
        """List emits its own ``{Model}ListItem`` schema + serializer."""
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert len(schemas) == 1
        assert schemas[0].name == "UserListItem"

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert len(sers) == 1
        assert sers[0].function_name == "to_user_list_item"
        assert sers[0].schema_name == "UserListItem"

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.method == "POST"
        assert handler.path == "/search"
        assert handler.function_name == "list_users"
        assert handler.response_model == "list[UserListItem]"
        assert handler.return_type == "list[UserListItem]"
        assert handler.serializer_fn == "to_user_list_item"

    def test_list_test_case(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))
        tests = [r for r in result if isinstance(r, TestCase)]
        assert tests[0].op_name == "list"
        assert tests[0].method == "post"
        assert tests[0].path == "/search"
        assert tests[0].is_list_response is True

    def test_list_without_extensions_has_single_bodyless_search(self):
        """Bare list op emits only one POST /search with no body."""
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert len(handlers) == 1
        assert handlers[0].method == "POST"
        assert handlers[0].path == "/search"
        assert handlers[0].params == []
        assert handlers[0].request_schema is None
        assert not any(isinstance(r, ExtensionSchema) for r in result)
        assert not any(isinstance(r, EnumClass) for r in result)

    def test_list_with_filters_emits_filter_schemas(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        opts = List.Options(
            fields=_FIELDS,
            filters=FilterConfig(fields=["name"]),
        )
        result = list(List().build(ctx, opts))

        ext_names = [r.name for r in result if isinstance(r, ExtensionSchema)]
        assert "UserFilterCondition" in ext_names
        assert "UserSearchRequest" in ext_names
        assert "UserPage" not in ext_names  # no pagination configured

    def test_list_with_ordering_emits_sort_field_enum_and_clause(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        opts = List.Options(
            fields=_FIELDS,
            ordering=OrderConfig(fields=["name", "age"], default="name"),
        )
        result = list(List().build(ctx, opts))

        enums = [r for r in result if isinstance(r, EnumClass)]
        assert len(enums) == 1
        assert enums[0].name == "UserSortField"
        assert enums[0].members == [("NAME", "name"), ("AGE", "age")]

        ext_names = [r.name for r in result if isinstance(r, ExtensionSchema)]
        assert "UserSortClause" in ext_names
        assert "UserSearchRequest" in ext_names

    def test_list_with_keyset_pagination_emits_page_and_search_handler(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        opts = List.Options(
            fields=_FIELDS,
            pagination=PaginateConfig(mode="keyset", cursor_field="id"),
        )
        result = list(List().build(ctx, opts))

        ext_names = [r.name for r in result if isinstance(r, ExtensionSchema)]
        assert "UserPage" in ext_names
        assert "UserSearchRequest" in ext_names

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert len(handlers) == 1
        search = handlers[0]
        assert search.method == "POST"
        assert search.path == "/search"
        assert search.function_name == "list_users"
        assert search.response_model == "UserPage"
        assert search.request_schema == "UserSearchRequest"
        assert search.body_context["pagination_mode"] == "keyset"
        assert search.body_context["cursor_field"] == "id"
        assert ("ingot", "apply_keyset_pagination") in search.extra_imports

    def test_list_with_offset_pagination_uses_offset_helper(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        opts = List.Options(
            fields=_FIELDS,
            pagination=PaginateConfig(mode="offset"),
        )
        result = list(List().build(ctx, opts))

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert len(handlers) == 1
        search = handlers[0]
        assert search.body_context["pagination_mode"] == "offset"
        assert ("ingot", "apply_offset_pagination") in search.extra_imports
        assert ("ingot", "apply_keyset_pagination") not in search.extra_imports

    def test_list_with_all_extensions_emits_everything(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        opts = List.Options(
            fields=_FIELDS,
            filters=FilterConfig(),
            ordering=OrderConfig(fields=["name"]),
            pagination=PaginateConfig(mode="keyset"),
        )
        result = list(List().build(ctx, opts))

        ext_names = {r.name for r in result if isinstance(r, ExtensionSchema)}
        assert ext_names == {
            "UserFilterCondition",
            "UserSortClause",
            "UserSearchRequest",
            "UserPage",
        }

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert len(handlers) == 1
        search = handlers[0]
        assert search.body_context["has_filter"] is True
        assert search.body_context["has_sort"] is True
        assert search.body_context["pagination_mode"] == "keyset"
        assert ("ingot", "apply_filters") in search.extra_imports
        assert ("ingot", "apply_ordering") in search.extra_imports
        assert ("ingot", "apply_keyset_pagination") in search.extra_imports

        tests = [r for r in result if isinstance(r, TestCase)]
        assert len(tests) == 1
        tc = tests[0]
        assert tc.method == "post"
        assert tc.path == "/search"
        assert tc.has_request_body is True
        assert tc.request_schema == "UserSearchRequest"
        assert tc.is_list_response is False

    def test_list_filters_default_to_all_list_fields(self):
        """FilterConfig with no fields uses the list op's full field set."""
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        opts = List.Options(fields=_FIELDS, filters=FilterConfig())
        result = list(List().build(ctx, opts))

        filter_schema = next(
            r
            for r in result
            if isinstance(r, ExtensionSchema)
            and r.name == "UserFilterCondition"
        )
        assert filter_schema.body_context["allowed_fields"] == ["name", "age"]


# -------------------------------------------------------------------
# Create
# -------------------------------------------------------------------


class TestCreate:
    """Tests for Create operation."""

    def test_create_emits_schema_and_handler(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="create"))
        opts = _FieldsOpts(fields=_FIELDS)
        result = list(Create().build(ctx, opts))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert len(schemas) == 1
        assert schemas[0].name == "UserCreateRequest"

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert handlers[0].method == "POST"
        assert handlers[0].status_code == 201
        assert handlers[0].request_schema == "UserCreateRequest"

    def test_create_test_case(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="create"))
        opts = _FieldsOpts(fields=_FIELDS)
        result = list(Create().build(ctx, opts))
        tests = [r for r in result if isinstance(r, TestCase)]
        assert tests[0].status_success == 201
        assert tests[0].status_invalid == 422
        assert tests[0].has_request_body is True
        assert tests[0].request_schema == "UserCreateRequest"


# -------------------------------------------------------------------
# Update
# -------------------------------------------------------------------


class TestUpdate:
    """Tests for Update operation."""

    def test_update_with_fields(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="update"))
        opts = _FieldsOpts(fields=_FIELDS)
        result = list(Update().build(ctx, opts))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert len(schemas) == 1
        assert schemas[0].name == "UserUpdateRequest"
        # All fields should be optional
        for f in schemas[0].fields:
            assert f.optional is True

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert handlers[0].method == "PATCH"

    def test_update_test_case(self):
        resource = ResourceConfig(
            model="app.models.User",
            pk="user_id",
            pk_type="int",
        )
        ctx = _operation_ctx(resource, OperationConfig(name="update"))
        opts = _FieldsOpts(fields=_FIELDS)
        result = list(Update().build(ctx, opts))
        tests = [r for r in result if isinstance(r, TestCase)]
        assert tests[0].op_name == "update"
        assert tests[0].path == "/{user_id}"
        assert tests[0].status_not_found == 404
        assert tests[0].status_invalid == 422


# -------------------------------------------------------------------
# Delete
# -------------------------------------------------------------------


class TestDelete:
    """Tests for Delete operation."""

    def test_delete_basic(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="delete"))
        result = list(Delete().build(ctx, _Empty()))

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert len(handlers) == 1
        assert handlers[0].method == "DELETE"
        assert handlers[0].status_code == 204
        assert handlers[0].function_name == "delete_user"

    def test_delete_test_case(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="delete"))
        result = list(Delete().build(ctx, _Empty()))
        tests = [r for r in result if isinstance(r, TestCase)]
        assert tests[0].status_success == 204
        assert tests[0].status_not_found == 404

    def test_delete_pk_param(self):
        resource = ResourceConfig(
            model="app.models.Item",
            pk="item_id",
            pk_type="str",
        )
        ctx = _operation_ctx(resource, OperationConfig(name="delete"))
        result = list(Delete().build(ctx, _Empty()))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.path == "/{item_id}"
        assert handler.params[0].name == "item_id"
        assert handler.params[0].annotation == "str"


# -------------------------------------------------------------------
# Action
# -------------------------------------------------------------------


class TestAction:
    """Tests for Action operation."""

    def test_action_object_level(self):
        """Object-level action includes pk in path."""
        resource = ResourceConfig(model="blog.models.Post")

        @dataclass
        class _Info:
            is_object_action: bool = True
            response_class: str | None = "PostResult"
            request_class: str | None = "PostRequest"
            model_param_name: str | None = "post"

        op_config = OperationConfig(
            name="publish",
            fn="blog.actions.publish",
        )
        ctx = _operation_ctx(resource, op_config)

        from kiln.operations.action import Action

        opts = Action.Options(fn="blog.actions.publish")

        with patch(
            "kiln.operations.action.introspect_action_fn",
            return_value=_Info(),
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.path == "/{id}/publish"
        assert handler.function_name == "publish_action"
        assert handler.response_model == "PostResult"

        test = next(r for r in result if isinstance(r, TestCase))
        assert test.status_not_found == 404
        assert test.action_name == "publish"

    def test_action_collection_level(self):
        """Collection-level action has no pk in path."""
        resource = ResourceConfig(model="blog.models.Post")

        @dataclass
        class _Info:
            is_object_action: bool = False
            response_class: str | None = None
            request_class: str | None = None
            model_param_name: str | None = None

        op_config = OperationConfig(
            name="bulk_import",
            fn="blog.actions.bulk_import",
        )
        ctx = _operation_ctx(resource, op_config)

        from kiln.operations.action import Action

        opts = Action.Options(fn="blog.actions.bulk_import")

        with patch(
            "kiln.operations.action.introspect_action_fn",
            return_value=_Info(),
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.path == "/bulk-import"

        test = next(r for r in result if isinstance(r, TestCase))
        assert test.status_not_found is None
