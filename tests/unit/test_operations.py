"""Tests for be.operations — scaffold, routing, crud, action."""

from unittest.mock import patch

from pydantic import BaseModel

from be.config.schema import (
    App,
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    FieldSpec,
    FilterConfig,
    ModifierConfig,
    OperationConfig,
    OrderConfig,
    PaginateConfig,
    ProjectConfig,
    ResourceConfig,
    StructuredFilterField,
    TelemetryConfig,
)
from be.operations.auth import Auth as AuthOp
from be.operations.create import Create
from be.operations.delete import Delete
from be.operations.filter import Filter
from be.operations.get import Get
from be.operations.list import List
from be.operations.order import Order
from be.operations.paginate import Paginate
from be.operations.routing import ProjectRouter, Router
from be.operations.scaffold import AuthScaffold, Scaffold
from be.operations.types import (
    EnumClass,
    Field,
    RouteHandler,
    SchemaClass,
    SerializerFn,
    TestCase,
    _field_dicts,
)
from be.operations.update import Update
from foundry.engine import BuildContext
from foundry.operation import EmptyOptions
from foundry.outputs import StaticFile
from foundry.scope import PROJECT, Scope, ScopeTree
from foundry.store import BuildStore

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


class MinimalConfig(BaseModel):
    """Minimal project config for tests."""

    auth: AuthConfig | None = None
    telemetry: TelemetryConfig | None = None
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
MODIFIER_SCOPE = Scope(
    name="modifier",
    config_key="modifiers",
    parent=OPERATION_SCOPE,
)
SCOPE_TREE = ScopeTree(
    [PROJECT, APP_SCOPE, RESOURCE_SCOPE, OPERATION_SCOPE, MODIFIER_SCOPE],
)


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


def _minimal_auth() -> AuthConfig:
    """Cheapest valid AuthConfig for tests that need one.

    Many tests now need ``project.auth`` configured solely to
    satisfy :class:`ProjectConfig`'s action-framework validator;
    bundling the boilerplate keeps the noise out of test bodies.
    """
    return AuthConfig(
        credentials_schema="myapp.auth.LoginCredentials",
        session_schema="myapp.auth.Session",
        validate_fn="myapp.auth.validate",
    )


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
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
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
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
            )
        )
        ctx = _project_ctx(config)
        assert AuthScaffold().when(ctx) is True

    def test_auth_files(self):
        """Auth config emits __init__, dependencies, and router."""
        config = MinimalConfig(
            auth=AuthConfig(
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        paths = [f.path for f in result]
        assert paths == [
            "auth/__init__.py",
            "auth/dependencies.py",
            "auth/router.py",
        ]

    def test_router_context_splits_user_dotted_paths(self):
        """The router template gets the credentials schema + validator split."""
        config = MinimalConfig(
            auth=AuthConfig(
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate_login",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        router = next(f for f in result if f.path == "auth/router.py")
        assert router.context["creds_module"] == "myapp.auth"
        assert router.context["creds_name"] == "LoginCredentials"
        assert router.context["session_module"] == "myapp.auth"
        assert router.context["session_name"] == "Session"
        assert router.context["validate_module"] == "myapp.auth"
        assert router.context["validate_name"] == "validate_login"

    def test_default_sources_bearer_only(self):
        """Default sources=['bearer'] flows into deps + router context."""
        config = MinimalConfig(
            auth=AuthConfig(
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        deps = next(f for f in result if f.path == "auth/dependencies.py")
        router = next(f for f in result if f.path == "auth/router.py")
        assert deps.context["sources"] == ["bearer"]
        assert router.context["sources"] == ["bearer"]

    def test_sources_both_thread_cookie_fields(self):
        """sources=['bearer','cookie'] carries cookie_* into the router."""
        config = MinimalConfig(
            auth=AuthConfig(
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
                sources=["bearer", "cookie"],
                cookie_name="session",
                cookie_secure=False,
                cookie_samesite="strict",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        router = next(f for f in result if f.path == "auth/router.py")
        assert router.context["sources"] == ["bearer", "cookie"]
        assert router.context["cookie_name"] == "session"
        assert router.context["cookie_secure"] is False
        assert router.context["cookie_samesite"] == "strict"

    def test_samesite_none_requires_secure(self):
        """Config validator rejects SameSite=None without Secure."""
        import pytest

        with pytest.raises(ValueError, match="cookie_samesite='none'"):
            AuthConfig(
                sources=["cookie"],
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
                cookie_samesite="none",
                cookie_secure=False,
            )

    def test_session_store_threads_module_and_name(self):
        """Setting session_store splits its dotted path into both files."""
        config = MinimalConfig(
            auth=AuthConfig(
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
                session_store="myapp.revocation.revocations",
            )
        )
        ctx = _project_ctx(config)
        result = list(AuthScaffold().build(ctx, _Empty()))
        deps = next(f for f in result if f.path == "auth/dependencies.py")
        router = next(f for f in result if f.path == "auth/router.py")
        assert deps.context["store_module"] == "myapp.revocation"
        assert deps.context["store_name"] == "revocations"
        assert router.context["store_module"] == "myapp.revocation"
        assert router.context["store_name"] == "revocations"


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
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.verify",
            ),
            apps=[App(config=app_config, prefix="/blog")],
        )
        ctx = _project_ctx(config)
        result = list(ProjectRouter().build(ctx, _Empty()))
        sf = result[0]
        assert sf.context["has_auth"] is True
        assert sf.context["auth_module"] == "_generated.auth"


# -------------------------------------------------------------------
# Actions registry
# -------------------------------------------------------------------


class TestActions:
    """Tests for the app-scope Actions operation."""

    @staticmethod
    def _ctx(
        module: str,
        resources: list[ResourceConfig],
        store: BuildStore,
    ) -> BuildContext:
        """Mirror :class:`TestRouter._ctx` for the Actions op."""
        app = App(
            config=AppConfig(module=module, resources=resources),
            prefix="",
        )
        project = ProjectConfig(
            auth=_minimal_auth(),
            apps=[app],
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        app_id = "project.apps.0"
        store.register_instance(app_id, app)

        for index, resource in enumerate(resources):
            iid = f"{app_id}.resources.{index}"
            store.register_instance(iid, resource, parent=app_id)

        return BuildContext(
            config=project,
            scope=_ROUTER_APP_SCOPE,
            instance=app,
            instance_id=app_id,
            store=store,
        )

    def test_no_participating_resources_returns_empty(self):
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        resource = ResourceConfig(
            model="pkg.models.Post",
            operations=[OperationConfig(name="get")],
        )
        ctx = self._ctx("blog", [resource], store)

        result = list(Actions().build(ctx, _Empty()))
        assert result == []

    def test_dump_flag_emits_registry_with_default_guards(self):
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        resource = ResourceConfig(
            model="pkg.models.Post",
            include_actions_in_dump=True,
            operations=[
                OperationConfig(name="get"),
                OperationConfig(name="list"),
                OperationConfig(name="create"),
            ],
        )
        ctx = self._ctx("blog", [resource], store)

        result = list(Actions().build(ctx, _Empty()))
        assert len(result) == 1
        sf = result[0]
        assert sf.path == "blog/actions.py"

        resources_ctx = sf.context["resources"]
        assert len(resources_ctx) == 1
        entry = resources_ctx[0]
        assert entry["object_const"] == "POST_OBJECT_ACTIONS"
        assert entry["collection_const"] == "POST_COLLECTION_ACTIONS"
        assert entry["object_actions"] == [
            {"name": "get", "can": "always_true"},
        ]
        assert entry["collection_actions"] == [
            {"name": "list", "can": "always_true"},
            {"name": "create", "can": "always_true"},
        ]
        assert sf.context["guard_imports"] == []

    def test_can_path_registers_import_and_uses_bare_name(self):
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        resource = ResourceConfig(
            model="pkg.models.Post",
            include_actions_in_dump=True,
            operations=[
                OperationConfig(name="get", can="pkg.guards.can_get_post"),
                OperationConfig(
                    name="delete", can="pkg.guards.can_delete_post"
                ),
                OperationConfig(name="list"),
            ],
        )
        ctx = self._ctx("blog", [resource], store)

        sf = next(
            r
            for r in Actions().build(ctx, _Empty())
            if isinstance(r, StaticFile)
        )
        assert sf.context["guard_imports"] == [
            ("pkg.guards", ["can_delete_post", "can_get_post"]),
        ]
        entry = sf.context["resources"][0]
        assert entry["object_actions"] == [
            {"name": "get", "can": "can_get_post"},
            {"name": "delete", "can": "can_delete_post"},
        ]
        assert entry["collection_actions"] == [
            {"name": "list", "can": "always_true"},
        ]

    def test_resource_with_only_can_participates(self):
        """A resource that opts in via ``can`` alone still gets a registry."""
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        resource = ResourceConfig(
            model="pkg.models.Post",
            operations=[OperationConfig(name="get", can="pkg.g.can_get")],
        )
        ctx = self._ctx("blog", [resource], store)

        result = list(Actions().build(ctx, _Empty()))
        assert len(result) == 1

    def test_action_op_introspected_for_object_scope(self):
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        resource = ResourceConfig(
            model="tests.unit._action_stubs.StubModel",
            include_actions_in_dump=True,
            operations=[
                OperationConfig(
                    name="publish",
                    type="action",
                    fn="tests.unit._action_stubs.object_action_no_body",
                ),
                OperationConfig(
                    name="bulk_archive",
                    type="action",
                    fn="tests.unit._action_stubs.collection_action_no_body",
                ),
            ],
        )
        ctx = self._ctx("blog", [resource], store)

        sf = next(
            r
            for r in Actions().build(ctx, _Empty())
            if isinstance(r, StaticFile)
        )
        entry = sf.context["resources"][0]
        assert entry["object_actions"] == [
            {"name": "publish", "can": "always_true"},
        ]
        assert entry["collection_actions"] == [
            {"name": "bulk_archive", "can": "always_true"},
        ]

    def test_modifier_ops_skipped(self):
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        resource = ResourceConfig(
            model="pkg.models.Post",
            include_actions_in_dump=True,
            operations=[
                OperationConfig(name="list"),
                # An op with an unknown type should not appear in the
                # registry; modifiers carry ``type`` discriminators.
                OperationConfig(name="filter_thing", type="filter"),
            ],
        )
        ctx = self._ctx("blog", [resource], store)

        sf = next(
            r
            for r in Actions().build(ctx, _Empty())
            if isinstance(r, StaticFile)
        )
        entry = sf.context["resources"][0]
        assert entry["collection_actions"] == [
            {"name": "list", "can": "always_true"},
        ]
        assert entry["object_actions"] == []

    def test_multiple_resources_share_import_module(self):
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        post = ResourceConfig(
            model="pkg.models.Post",
            operations=[
                OperationConfig(name="get", can="pkg.guards.can_get_post"),
            ],
        )
        comment = ResourceConfig(
            model="pkg.models.Comment",
            operations=[
                OperationConfig(name="get", can="pkg.guards.can_get_comment"),
            ],
        )
        ctx = self._ctx("blog", [post, comment], store)

        sf = next(
            r
            for r in Actions().build(ctx, _Empty())
            if isinstance(r, StaticFile)
        )
        assert sf.context["guard_imports"] == [
            ("pkg.guards", ["can_get_comment", "can_get_post"]),
        ]
        consts = [r["object_const"] for r in sf.context["resources"]]
        assert consts == ["POST_OBJECT_ACTIONS", "COMMENT_OBJECT_ACTIONS"]

    def test_skips_non_participating_resources_in_mixed_app(self):
        from be.operations.actions import Actions

        store = BuildStore(scope_tree=_ROUTER_SCOPE_TREE)
        opted_in = ResourceConfig(
            model="pkg.models.Post",
            include_actions_in_dump=True,
            operations=[OperationConfig(name="get")],
        )
        opted_out = ResourceConfig(
            model="pkg.models.Tag",
            operations=[OperationConfig(name="get")],
        )
        ctx = self._ctx("blog", [opted_in, opted_out], store)

        sf = next(
            r
            for r in Actions().build(ctx, _Empty())
            if isinstance(r, StaticFile)
        )
        consts = [r["object_const"] for r in sf.context["resources"]]
        assert consts == ["POST_OBJECT_ACTIONS"]


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
        result, enum_imports = _field_dicts(fields)
        assert len(result) == 2
        assert result[0] == Field(name="title", py_type="str")
        assert result[1] == Field(name="count", py_type="int")
        assert enum_imports == []

    def test_field_dicts_enum(self):
        fields = [
            FieldSpec(
                name="status",
                type="enum",
                enum="myapp.models.Status",
            ),
        ]
        result, enum_imports = _field_dicts(fields)
        assert len(result) == 1
        assert result[0] == Field(name="status", py_type="Status")
        assert enum_imports == [("myapp.models", "Status")]

    def test_field_dicts_rejects_nested(self):
        import pytest

        fields = [
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[FieldSpec(name="id", type="uuid")],
            ),
        ]

        with pytest.raises(ValueError, match="only supported on read"):
            _field_dicts(fields)


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

    def test_get_with_nested_field_emits_sub_schema_and_serializer(self):
        """A nested field adds its own schema + serializer alongside main."""
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(name="title", type="str"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[
                    FieldSpec(name="id", type="uuid"),
                    FieldSpec(name="name", type="str"),
                ],
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert [s.name for s in schemas] == [
            "TaskResourceProjectNested",
            "TaskResource",
        ]

        # Parent schema's project field references the nested class name.
        parent = schemas[1]
        project_field = next(f for f in parent.fields if f.name == "project")
        assert project_field.py_type == "TaskResourceProjectNested"
        assert project_field.nested_serializer == (
            "to_task_resource_project_nested"
        )
        assert project_field.many is False

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert [s.function_name for s in sers] == [
            "to_task_resource_project_nested",
            "to_task_resource",
        ]

        nested_ser = sers[0]
        assert nested_ser.model_name == "Project"
        assert nested_ser.model_module == "blog.models"
        assert nested_ser.schema_name == "TaskResourceProjectNested"
        assert [f.name for f in nested_ser.fields] == ["id", "name"]

        main_ser = sers[1]
        assert main_ser.model_module == "blog.models"
        assert main_ser.model_name == "Task"

    def test_get_with_nested_many_wraps_in_list(self):
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="articles",
                type="nested",
                model="blog.models.Article",
                fields=[FieldSpec(name="id", type="uuid")],
                many=True,
            ),
        ]
        resource = ResourceConfig(model="blog.models.Author")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        parent = next(
            r
            for r in result
            if isinstance(r, SchemaClass) and r.name == "AuthorResource"
        )
        articles = next(f for f in parent.fields if f.name == "articles")
        assert articles.py_type == "list[AuthorResourceArticlesNested]"
        assert articles.many is True

    def test_get_with_nested_emits_selectinload_by_default(self):
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[FieldSpec(name="id", type="uuid")],
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == [
            "selectinload(Task.project)",
        ]
        assert ("sqlalchemy.orm", "selectinload") in handler.extra_imports

    def test_get_with_nested_load_override(self):
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[FieldSpec(name="id", type="uuid")],
                load="joined",
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == [
            "joinedload(Task.project)",
        ]
        assert ("sqlalchemy.orm", "joinedload") in handler.extra_imports

    def test_get_with_nested_in_nested_builds_chain(self):
        """Chains go parent→related→deeper; related model gets imported."""
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[
                    FieldSpec(name="id", type="uuid"),
                    FieldSpec(
                        name="owner",
                        type="nested",
                        model="blog.models.User",
                        fields=[FieldSpec(name="name", type="str")],
                        load="joined",
                    ),
                ],
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == [
            "selectinload(Task.project).joinedload(Project.owner)",
        ]
        # Both loader funcs and the intermediate model class are needed.
        assert ("sqlalchemy.orm", "selectinload") in handler.extra_imports
        assert ("sqlalchemy.orm", "joinedload") in handler.extra_imports
        assert ("blog.models", "Project") in handler.extra_imports

    def test_get_without_nested_has_empty_load_options(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == []
        assert not any(
            mod == "sqlalchemy.orm" for mod, _ in handler.extra_imports
        )

    def test_get_with_nested_in_nested(self):
        """Nested fields recurse: names accumulate down the path."""
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[
                    FieldSpec(name="id", type="uuid"),
                    FieldSpec(
                        name="owner",
                        type="nested",
                        model="blog.models.User",
                        fields=[FieldSpec(name="name", type="str")],
                    ),
                ],
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        # Deepest-first: owner, then project, then parent.
        assert [s.name for s in schemas] == [
            "TaskResourceProjectOwnerNested",
            "TaskResourceProjectNested",
            "TaskResource",
        ]

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert [s.function_name for s in sers] == [
            "to_task_resource_project_owner_nested",
            "to_task_resource_project_nested",
            "to_task_resource",
        ]

    def test_get_with_four_levels_of_nesting(self):
        """Recursion scales: 4 levels produce 4 sub-schemas + 4 sub-serializers.

        Tests that path accumulation, schema ordering (deepest-first),
        sub-serializer wiring, and the load-chain builder all compose
        down a 4-deep nested path with mixed load strategies at every
        level.
        """
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[
                    FieldSpec(
                        name="owner",
                        type="nested",
                        model="blog.models.User",
                        fields=[
                            FieldSpec(
                                name="team",
                                type="nested",
                                model="blog.models.Team",
                                fields=[
                                    FieldSpec(
                                        name="org",
                                        type="nested",
                                        model="blog.models.Org",
                                        fields=[
                                            FieldSpec(name="id", type="uuid"),
                                        ],
                                        load="subquery",
                                    ),
                                ],
                                load="joined",
                            ),
                        ],
                    ),
                ],
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        # Every level contributes one schema + one serializer; deepest first.
        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert [s.name for s in schemas] == [
            "TaskResourceProjectOwnerTeamOrgNested",
            "TaskResourceProjectOwnerTeamNested",
            "TaskResourceProjectOwnerNested",
            "TaskResourceProjectNested",
            "TaskResource",
        ]

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert [s.function_name for s in sers] == [
            "to_task_resource_project_owner_team_org_nested",
            "to_task_resource_project_owner_team_nested",
            "to_task_resource_project_owner_nested",
            "to_task_resource_project_nested",
            "to_task_resource",
        ]

        # Every intermediate sub-serializer references the next one down.
        project_ser = next(
            s for s in sers if s.function_name.endswith("_project_nested")
        )
        project_owner_field = next(
            f for f in project_ser.fields if f.name == "owner"
        )
        assert project_owner_field.nested_serializer == (
            "to_task_resource_project_owner_nested"
        )

        # Load chain spans all four levels, each with its configured strategy.
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == [
            "selectinload(Task.project)"
            ".selectinload(Project.owner)"
            ".joinedload(User.team)"
            ".subqueryload(Team.org)",
        ]

        # Every loader and every intermediate model class is imported.
        imports = handler.extra_imports
        assert ("sqlalchemy.orm", "selectinload") in imports
        assert ("sqlalchemy.orm", "joinedload") in imports
        assert ("sqlalchemy.orm", "subqueryload") in imports
        assert ("blog.models", "Project") in imports
        assert ("blog.models", "User") in imports
        assert ("blog.models", "Team") in imports

    def test_get_with_branching_nested_fields(self):
        """Multiple nested siblings at the same level each get their own chain.

        Sibling nested fields shouldn't interfere: their schemas,
        serializers, and load chains should all coexist independently.
        """
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[FieldSpec(name="name", type="str")],
            ),
            FieldSpec(
                name="tags",
                type="nested",
                model="blog.models.Tag",
                fields=[FieldSpec(name="name", type="str")],
                many=True,
            ),
            FieldSpec(
                name="owner",
                type="nested",
                model="blog.models.User",
                fields=[FieldSpec(name="email", type="email")],
                load="joined",
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        schema_names = {
            s.name
            for s in result
            if isinstance(s, SchemaClass) and s.body_template is None
        }
        assert {
            "TaskResource",
            "TaskResourceProjectNested",
            "TaskResourceTagsNested",
            "TaskResourceOwnerNested",
        } <= schema_names

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == [
            "selectinload(Task.project)",
            "selectinload(Task.tags)",
            "joinedload(Task.owner)",
        ]

    def test_get_with_nested_in_nested_many_chain(self):
        """``many=true`` inside a nested chain still produces a single chain."""
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[
                    FieldSpec(
                        name="tasks",
                        type="nested",
                        model="blog.models.Subtask",
                        fields=[FieldSpec(name="id", type="uuid")],
                        many=True,
                    ),
                ],
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=fields)))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == [
            "selectinload(Task.project).selectinload(Project.tasks)",
        ]


# -------------------------------------------------------------------
# List
# -------------------------------------------------------------------


def _templated_schemas(result: list[object]) -> list[SchemaClass]:
    """Filter to SchemaClass entries rendered via a custom template."""
    return [
        r
        for r in result
        if isinstance(r, SchemaClass) and r.body_template is not None
    ]


def _drive_list(
    resource: ResourceConfig,
    *,
    fields: list[FieldSpec] | None = None,
    store: BuildStore | None = None,
) -> BuildContext:
    """Run List at ``.operations.0`` and register its outputs.

    Extension-op tests call this first so ``find_list_outputs``
    has a SearchRequest and RouteHandler to amend.
    """
    effective_fields = fields or _FIELDS
    op_config = OperationConfig(
        name="list",
        fields=[f.model_dump() for f in effective_fields],
    )
    ctx = _operation_ctx(resource, op_config, store=store)
    outputs = list(
        List().build(ctx, List.Options(fields=effective_fields)),
    )
    ctx.store.add(ctx.instance_id, "list", *outputs)
    return ctx


def _drive_extension(
    parent_ctx: BuildContext,
    *,
    type_name: str,
    op_instance: object,
    options: BaseModel,
    extra_config: dict | None = None,
) -> list[object]:
    """Run a modifier op at the next free modifier-scope slot.

    Modifiers nest inside the parent list op (``parent_ctx``'s
    instance).  Registers the modifier under the list's
    instance_id so ``find_list_outputs`` resolves the parent
    correctly.  Returns the op's yielded outputs; any amendments
    it made to the parent's outputs are visible on the store.
    """
    store = parent_ctx.store
    list_id = parent_ctx.instance_id
    n = 0

    while f"{list_id}.modifiers.{n}" in store._instances:
        n += 1

    ext_id = f"{list_id}.modifiers.{n}"

    ext_config = ModifierConfig(type=type_name, **(extra_config or {}))
    store.register_instance(ext_id, ext_config, parent=list_id)
    ext_ctx = BuildContext(
        config=parent_ctx.config,
        scope=MODIFIER_SCOPE,
        instance=ext_config,
        instance_id=ext_id,
        store=store,
    )
    outputs = list(op_instance.build(ext_ctx, options))
    store.add(ext_id, type_name, *outputs)
    return outputs


class TestList:
    """Tests for the bare List op (no extensions)."""

    def test_emits_list_item_schema_and_serializer(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert {s.name for s in schemas} == {
            "UserListItem",
            "UserSearchRequest",
        }

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert len(sers) == 1
        assert sers[0].function_name == "to_user_list_item"

    def test_search_request_starts_empty(self):
        """With no extensions, SearchRequest is an empty Pydantic model."""
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))

        search_req = next(
            s
            for s in result
            if isinstance(s, SchemaClass) and s.name == "UserSearchRequest"
        )
        assert search_req.body_context["has_filter"] is False
        assert search_req.body_context["has_sort"] is False
        assert search_req.body_context["pagination_mode"] is None

    def test_handler_is_bare_post_search(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.method == "POST"
        assert handler.path == "/search"
        assert handler.function_name == "list_users"
        assert handler.request_schema == "UserSearchRequest"
        assert handler.response_model == "list[UserListItem]"
        assert handler.body_context["pagination_mode"] is None
        assert ("sqlalchemy", "select") in handler.extra_imports
        assert not any(
            mod == "ingot" or mod.startswith("ingot.")
            for mod, _ in handler.extra_imports
        )

    def test_test_case(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))
        tests = [r for r in result if isinstance(r, TestCase)]
        assert tests[0].op_name == "list"
        assert tests[0].method == "post"
        assert tests[0].path == "/search"
        assert tests[0].is_list_response is True

    def test_list_with_nested_emits_load_options(self):
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="assignees",
                type="nested",
                model="blog.models.User",
                fields=[FieldSpec(name="id", type="uuid")],
                many=True,
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=fields)))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["load_options"] == [
            "selectinload(Task.assignees)",
        ]
        assert ("sqlalchemy.orm", "selectinload") in handler.extra_imports

    def test_list_with_nested_field_emits_scoped_sub_schema(self):
        """List-item nested schema is scoped under ListItem, not Resource."""
        fields = [
            FieldSpec(name="id", type="uuid"),
            FieldSpec(
                name="project",
                type="nested",
                model="blog.models.Project",
                fields=[FieldSpec(name="name", type="str")],
            ),
        ]
        resource = ResourceConfig(model="blog.models.Task")
        ctx = _operation_ctx(resource, OperationConfig(name="list"))
        result = list(List().build(ctx, List.Options(fields=fields)))

        schemas = [
            r
            for r in result
            if isinstance(r, SchemaClass) and r.body_template is None
        ]
        # Scoping under the list item keeps names distinct from a Get
        # op's nested schemas on the same resource.
        assert [s.name for s in schemas] == [
            "TaskListItemProjectNested",
            "TaskListItem",
        ]

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert [s.function_name for s in sers] == [
            "to_task_list_item_project_nested",
            "to_task_list_item",
        ]


class TestFilter:
    """Tests for the Filter extension op."""

    def test_emits_filter_condition_schema(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        outputs = _drive_extension(
            list_ctx,
            type_name="filter",
            op_instance=Filter(),
            options=FilterConfig(
                fields=[
                    StructuredFilterField(name="name", values="free_text"),
                ],
            ),
        )
        schemas = [r for r in outputs if isinstance(r, SchemaClass)]
        assert [s.name for s in schemas] == ["UserFilterCondition"]
        assert schemas[0].body_context["allowed_fields"] == ["name"]

    def test_amends_search_request_and_handler(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        _drive_extension(
            list_ctx,
            type_name="filter",
            op_instance=Filter(),
            options=FilterConfig(
                fields=[
                    StructuredFilterField(name="name", values="free_text"),
                ],
            ),
        )
        search_req = _find_output(
            list_ctx.store, SchemaClass, name="UserSearchRequest"
        )
        handler = _find_handler(list_ctx.store, path="/search")
        assert search_req.body_context["has_filter"] is True
        assert handler.body_context["has_filter"] is True
        assert ("ingot.filters", "apply_filters") in handler.extra_imports


class TestOrder:
    """Tests for the Order extension op."""

    def test_emits_sort_field_enum_and_clause(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        outputs = _drive_extension(
            list_ctx,
            type_name="order",
            op_instance=Order(),
            options=OrderConfig(fields=["name", "age"], default="name"),
        )
        enums = [r for r in outputs if isinstance(r, EnumClass)]
        assert len(enums) == 1
        assert enums[0].name == "UserSortField"
        assert enums[0].members == [("NAME", "name"), ("AGE", "age")]

        clause = next(
            r
            for r in outputs
            if isinstance(r, SchemaClass) and r.name == "UserSortClause"
        )
        assert clause.body_template.endswith("sort_clause.py.j2")

    def test_stamps_sort_defaults_onto_handler(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        _drive_extension(
            list_ctx,
            type_name="order",
            op_instance=Order(),
            options=OrderConfig(
                fields=["name"],
                default="name",
                default_dir="desc",
            ),
        )
        handler = _find_handler(list_ctx.store, path="/search")
        assert handler.body_context["has_sort"] is True
        assert handler.body_context["default_sort_field"] == "name"
        assert handler.body_context["default_sort_dir"] == "desc"
        assert ("ingot.ordering", "apply_ordering") in handler.extra_imports


class TestPaginate:
    """Tests for the Paginate extension op."""

    def test_keyset_emits_page_and_wires_handler(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        outputs = _drive_extension(
            list_ctx,
            type_name="paginate",
            op_instance=Paginate(),
            options=PaginateConfig(mode="keyset", cursor_field="id"),
        )
        page = next(
            r
            for r in outputs
            if isinstance(r, SchemaClass) and r.name == "UserPage"
        )
        assert page.body_context["mode"] == "keyset"

        handler = _find_handler(list_ctx.store, path="/search")
        assert handler.response_model == "UserPage"
        assert handler.return_type == "UserPage"
        assert handler.body_context["pagination_mode"] == "keyset"
        assert handler.body_context["cursor_field"] == "id"
        keyset = ("ingot.pagination", "apply_keyset_pagination")
        assert keyset in handler.extra_imports

    def test_offset_uses_offset_helper(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        _drive_extension(
            list_ctx,
            type_name="paginate",
            op_instance=Paginate(),
            options=PaginateConfig(mode="offset"),
        )
        handler = _find_handler(list_ctx.store, path="/search")
        assert handler.body_context["pagination_mode"] == "offset"
        offset = ("ingot.pagination", "apply_offset_pagination")
        keyset = ("ingot.pagination", "apply_keyset_pagination")
        assert offset in handler.extra_imports
        assert keyset not in handler.extra_imports

    def test_flips_list_test_case_is_list_response(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        _drive_extension(
            list_ctx,
            type_name="paginate",
            op_instance=Paginate(),
            options=PaginateConfig(mode="keyset"),
        )
        tc = next(
            t
            for t in list_ctx.store.outputs_under(
                "project.apps.0.resources.0", TestCase
            )
            if t.op_name == "list"
        )
        assert tc.is_list_response is False


class TestExecutionGates:
    """Tests for execution-time ``can`` gates on CRUD ops."""

    def test_get_no_can_no_gate_imports(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert "gate_specs_const" not in handler.body_context
        assert ("ingot.actions", "find_can") not in handler.extra_imports

    def test_get_with_can_wires_object_gate(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(
            resource,
            OperationConfig(name="get", can="app.guards.can_get_user"),
        )
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["gate_specs_const"] == "USER_OBJECT_ACTIONS"
        assert handler.body_context["gate_op_name"] == "get"
        assert ("ingot.actions", "find_can") in handler.extra_imports
        assert ("fastapi", "HTTPException") in handler.extra_imports

    def test_create_with_can_wires_collection_gate(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(
            resource,
            OperationConfig(name="create", can="app.guards.can_create"),
        )
        result = list(Create().build(ctx, _FieldsOpts(fields=_FIELDS)))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert (
            handler.body_context["gate_specs_const"]
            == "USER_COLLECTION_ACTIONS"
        )
        assert handler.body_context["gate_op_name"] == "create"

    def test_update_with_can_sets_gate_context(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(
            resource,
            OperationConfig(name="update", can="app.guards.can_update"),
        )
        result = list(Update().build(ctx, _FieldsOpts(fields=_FIELDS)))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["gate_specs_const"] == "USER_OBJECT_ACTIONS"

    def test_update_without_can_has_no_gate_context(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(resource, OperationConfig(name="update"))
        result = list(Update().build(ctx, _FieldsOpts(fields=_FIELDS)))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert "gate_specs_const" not in handler.body_context

    def test_delete_with_can_sets_gate_context(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _operation_ctx(
            resource,
            OperationConfig(name="delete", can="app.guards.can_delete"),
        )
        result = list(Delete().build(ctx, EmptyOptions()))
        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["gate_specs_const"] == "USER_OBJECT_ACTIONS"


class TestPermissions:
    """Tests for the resource-scope Permissions op."""

    @staticmethod
    def _ctx(resource: ResourceConfig) -> BuildContext:
        store = BuildStore(scope_tree=SCOPE_TREE)
        resource_id = "project.apps.0.resources.0"
        store.register_instance(resource_id, resource)
        return BuildContext(
            config=MinimalConfig(),
            scope=Scope(
                name="resource", config_key="resources", parent=PROJECT
            ),
            instance=resource,
            instance_id=resource_id,
            store=store,
        )

    def test_when_false_skips(self):
        from be.operations.permissions import Permissions

        resource = ResourceConfig(model="app.models.Post")
        assert Permissions().when(self._ctx(resource)) is False

    def test_when_true_emits_two_handlers_and_two_test_cases(self):
        from be.operations.permissions import Permissions

        resource = ResourceConfig(
            model="app.models.Post",
            permissions_endpoint=True,
        )
        result = list(Permissions().build(self._ctx(resource), _Empty()))
        handlers = [r for r in result if isinstance(r, RouteHandler)]
        cases = [r for r in result if isinstance(r, TestCase)]

        assert [h.path for h in handlers] == [
            "/{id}/permissions",
            "/permissions",
        ]
        assert all(h.method == "GET" for h in handlers)
        assert all(h.response_model == "list[ActionRef]" for h in handlers)
        assert all(
            h.response_schema_module == "ingot.actions" for h in handlers
        )
        assert [c.path for c in cases] == [
            "/{id}/permissions",
            "/permissions",
        ]

    def test_handlers_import_registry_and_runtime(self):
        from be.operations.permissions import Permissions

        resource = ResourceConfig(
            model="app.models.Post",
            permissions_endpoint=True,
        )
        result = list(Permissions().build(self._ctx(resource), _Empty()))
        handlers = [r for r in result if isinstance(r, RouteHandler)]
        object_handler, collection_handler = handlers

        # Object handler imports the object spec; collection handler
        # imports the collection spec.  Both share the runtime helper.
        assert ("ingot.actions", "available_actions") in (
            object_handler.extra_imports
        )
        assert ("ingot.actions", "ActionRef") in object_handler.extra_imports
        assert any(
            mod.endswith(".actions") and name == "POST_OBJECT_ACTIONS"
            for mod, name in object_handler.extra_imports
        )
        assert any(
            mod.endswith(".actions") and name == "POST_COLLECTION_ACTIONS"
            for mod, name in collection_handler.extra_imports
        )


class TestAuthForcesSessionForActions:
    """Auth threads ``session`` into handlers on dump-enabled resources.

    The action framework references ``session`` in the generated
    serializer regardless of per-op ``require_auth``; without
    force-include, a resource with ``require_auth=False`` and
    ``include_actions_in_dump=True`` would generate code that
    references an undeclared parameter.
    """

    @staticmethod
    def _config_with_auth() -> MinimalConfig:
        return MinimalConfig(auth=_minimal_auth())

    def test_dump_resource_threads_session_even_without_require_auth(self):
        resource = ResourceConfig(
            model="app.models.User",
            require_auth=False,
            include_actions_in_dump=True,
            operations=[OperationConfig(name="get")],
        )
        store = BuildStore(scope_tree=SCOPE_TREE)
        resource_id = "project.apps.0.resources.0"
        store.register_instance(resource_id, resource)
        store.add(
            resource_id,
            "get",
            RouteHandler(
                method="GET",
                path="/{id}",
                function_name="get_user",
                op_name="get",
            ),
        )
        ctx = BuildContext(
            config=self._config_with_auth(),
            scope=Scope(
                name="resource", config_key="resources", parent=PROJECT
            ),
            instance=resource,
            instance_id=resource_id,
            store=store,
        )

        list(AuthOp().build(ctx, _Empty()))

        handler = store.outputs_under(resource_id, RouteHandler)[0]
        assert any("Depends(get_session)" in dep for dep in handler.extra_deps)

    def test_no_dump_no_force(self):
        resource = ResourceConfig(
            model="app.models.User",
            require_auth=False,
            operations=[OperationConfig(name="get")],
        )
        store = BuildStore(scope_tree=SCOPE_TREE)
        resource_id = "project.apps.0.resources.0"
        store.register_instance(resource_id, resource)
        store.add(
            resource_id,
            "get",
            RouteHandler(
                method="GET",
                path="/{id}",
                function_name="get_user",
                op_name="get",
            ),
        )
        ctx = BuildContext(
            config=self._config_with_auth(),
            scope=Scope(
                name="resource", config_key="resources", parent=PROJECT
            ),
            instance=resource,
            instance_id=resource_id,
            store=store,
        )

        list(AuthOp().build(ctx, _Empty()))

        handler = store.outputs_under(resource_id, RouteHandler)[0]
        assert handler.extra_deps == []


class TestGetIncludesActions:
    """Get with ``include_actions_in_dump=True`` wires the dump path."""

    def test_schema_gets_actions_field(self):
        resource = ResourceConfig(
            model="app.models.User",
            include_actions_in_dump=True,
        )
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))

        schema = next(
            r
            for r in result
            if isinstance(r, SchemaClass) and r.name == "UserResource"
        )
        action_fields = [f for f in schema.fields if f.name == "actions"]
        assert len(action_fields) == 1
        assert action_fields[0].py_type == "list[ActionRef]"

    def test_serializer_includes_actions(self):
        resource = ResourceConfig(
            model="app.models.User",
            include_actions_in_dump=True,
        )
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))

        ser = next(r for r in result if isinstance(r, SerializerFn))
        assert ser.include_actions is True

    def test_handler_marks_serializer_async(self):
        resource = ResourceConfig(
            model="app.models.User",
            include_actions_in_dump=True,
        )
        ctx = _operation_ctx(resource, OperationConfig(name="get"))
        result = list(Get().build(ctx, _FieldsOpts(fields=_FIELDS)))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["serializer_async"] is True


class TestListIncludesActions:
    """List with ``include_actions_in_dump=True`` wires dump + filter."""

    @staticmethod
    def _ctx(resource: ResourceConfig) -> BuildContext:
        return _operation_ctx(resource, OperationConfig(name="list"))

    def test_list_item_schema_gets_actions_field(self):
        resource = ResourceConfig(
            model="app.models.User",
            include_actions_in_dump=True,
        )
        result = list(
            List().build(self._ctx(resource), List.Options(fields=_FIELDS))
        )

        schema = next(
            r
            for r in result
            if isinstance(r, SchemaClass) and r.name == "UserListItem"
        )
        action_fields = [f for f in schema.fields if f.name == "actions"]
        assert len(action_fields) == 1
        assert action_fields[0].py_type == "list[ActionRef]"

    def test_handler_imports_filter_helpers_and_collection_specs(self):
        resource = ResourceConfig(
            model="app.models.User",
            include_actions_in_dump=True,
        )
        result = list(
            List().build(self._ctx(resource), List.Options(fields=_FIELDS))
        )

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert ("ingot.actions", "filter_visible") in handler.extra_imports
        assert ("ingot.actions", "find_can") in handler.extra_imports
        assert any(
            mod.endswith(".actions") and name == "USER_COLLECTION_ACTIONS"
            for mod, name in handler.extra_imports
        )
        assert handler.body_context["include_actions"] is True
        assert handler.body_context["serializer_async"] is True
        assert (
            handler.body_context["collection_specs_const"]
            == "USER_COLLECTION_ACTIONS"
        )

    def test_no_action_imports_when_flag_off(self):
        resource = ResourceConfig(model="app.models.User")
        result = list(
            List().build(self._ctx(resource), List.Options(fields=_FIELDS))
        )

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert not any(
            mod == "ingot.actions" for mod, _ in handler.extra_imports
        )
        assert handler.body_context.get("include_actions", False) is False
        assert handler.body_context.get("serializer_async", False) is False


class TestListExtensionsCompose:
    """All three extensions together on one list."""

    def test_filter_order_paginate_all_wire_in(self):
        resource = ResourceConfig(model="app.models.User")
        list_ctx = _drive_list(resource)
        _drive_extension(
            list_ctx,
            type_name="filter",
            op_instance=Filter(),
            options=FilterConfig(
                fields=[
                    StructuredFilterField(name="name", values="free_text"),
                ],
            ),
        )
        _drive_extension(
            list_ctx,
            type_name="order",
            op_instance=Order(),
            options=OrderConfig(fields=["name"], default="name"),
        )
        _drive_extension(
            list_ctx,
            type_name="paginate",
            op_instance=Paginate(),
            options=PaginateConfig(mode="keyset"),
        )
        handler = _find_handler(list_ctx.store, path="/search")
        assert handler.body_context["has_filter"] is True
        assert handler.body_context["has_sort"] is True
        assert handler.body_context["pagination_mode"] == "keyset"
        keyset = ("ingot.pagination", "apply_keyset_pagination")
        assert ("ingot.filters", "apply_filters") in handler.extra_imports
        assert ("ingot.ordering", "apply_ordering") in handler.extra_imports
        assert keyset in handler.extra_imports
        assert handler.response_model == "UserPage"


def _find_output(store: BuildStore, output_type: type, *, name: str):
    """Find a single output by type + .name in a store."""
    matches = [
        o
        for o in store.outputs_under("project.apps.0.resources.0", output_type)
        if getattr(o, "name", None) == name
    ]
    assert len(matches) == 1, (
        f"expected one {output_type.__name__} named {name}"
    )
    return matches[0]


def _find_handler(store: BuildStore, *, path: str) -> RouteHandler:
    matches = [
        h
        for h in store.outputs_under("project.apps.0.resources.0", RouteHandler)
        if h.path == path
    ]
    assert len(matches) == 1, f"expected one handler at {path}"
    return matches[0]


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
        # Required fields must be carried through so the generated
        # success test posts a valid body (regression: empty-dict
        # bodies were returning 422 for non-empty CreateRequests).
        assert tests[0].request_fields == [
            {"name": "name", "py_type": "str"},
            {"name": "age", "py_type": "int"},
        ]


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
        from be.operations._introspect import IntrospectedAction

        resource = ResourceConfig(model="blog.models.Post")

        info = IntrospectedAction(
            model_param_name="post",
            model_class_param_name=None,
            request_class="PostRequest",
            request_module="blog.actions",
            response_class="PostResult",
            response_module="blog.actions",
        )

        op_config = OperationConfig(
            name="publish",
            type="action",
            fn="blog.actions.publish",
        )
        ctx = _operation_ctx(resource, op_config)

        from be.operations.action import Action

        opts = Action.Options(fn="blog.actions.publish")

        with patch(
            "be.operations.action.introspect_action_fn",
            return_value=info,
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.path == "/{id}/publish"
        assert handler.function_name == "publish_action"
        assert handler.response_model == "PostResult"
        assert handler.request_schema_module == "blog.actions"
        assert handler.response_schema_module == "blog.actions"
        assert handler.status_code is None

        test = next(r for r in result if isinstance(r, TestCase))
        assert test.status_not_found == 404
        assert test.status_success == 200
        assert test.action_name == "publish"

    def test_action_collection_level(self):
        """Collection-level action has no pk in path."""
        from be.operations._introspect import IntrospectedAction

        resource = ResourceConfig(model="blog.models.Post")

        info = IntrospectedAction(
            model_param_name=None,
            model_class_param_name=None,
            request_class=None,
            request_module=None,
            response_class="BulkResult",
            response_module="blog.actions",
        )

        op_config = OperationConfig(
            name="bulk_import",
            type="action",
            fn="blog.actions.bulk_import",
        )
        ctx = _operation_ctx(resource, op_config)

        from be.operations.action import Action

        opts = Action.Options(fn="blog.actions.bulk_import")

        with patch(
            "be.operations.action.introspect_action_fn",
            return_value=info,
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.path == "/bulk-import"

        test = next(r for r in result if isinstance(r, TestCase))
        assert test.status_not_found is None

    def test_action_returns_none_emits_204(self):
        """``-> None`` action: 204 status, no response model, no return."""
        from be.operations._introspect import IntrospectedAction

        resource = ResourceConfig(model="blog.models.Post")

        info = IntrospectedAction(
            model_param_name="post",
            model_class_param_name=None,
            request_class=None,
            request_module=None,
            response_class=None,
            response_module=None,
        )

        op_config = OperationConfig(
            name="archive",
            type="action",
            fn="blog.actions.archive",
        )
        ctx = _operation_ctx(resource, op_config)

        from be.operations.action import Action

        opts = Action.Options(fn="blog.actions.archive")

        with patch(
            "be.operations.action.introspect_action_fn",
            return_value=info,
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.status_code == 204
        assert handler.response_model is None
        assert handler.return_type == "None"
        assert handler.body_context["returns_none"] is True

        test = next(r for r in result if isinstance(r, TestCase))
        assert test.status_success == 204

    def test_action_status_code_override(self):
        """Caller-supplied ``status_code`` wins over the framework default."""
        from be.operations._introspect import IntrospectedAction

        resource = ResourceConfig(model="blog.models.Post")

        info = IntrospectedAction(
            model_param_name="post",
            model_class_param_name=None,
            request_class=None,
            request_module=None,
            response_class="PostResource",
            response_module="blog.actions",
        )

        op_config = OperationConfig(
            name="publish",
            type="action",
            fn="blog.actions.publish",
        )
        ctx = _operation_ctx(resource, op_config)

        from be.operations.action import Action

        opts = Action.Options(fn="blog.actions.publish", status_code=202)

        with patch(
            "be.operations.action.introspect_action_fn",
            return_value=info,
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.status_code == 202

        test = next(r for r in result if isinstance(r, TestCase))
        assert test.status_success == 202

    def test_action_model_class_param_propagates_to_body_context(self):
        """``type[X]`` param name flows to the template so it can pass it."""
        from be.operations._introspect import IntrospectedAction

        resource = ResourceConfig(model="blog.models.Post")

        info = IntrospectedAction(
            model_param_name=None,
            model_class_param_name="model_cls",
            request_class="UploadRequest",
            request_module="ingot.files",
            response_class="UploadResponse",
            response_module="ingot.files",
        )

        op_config = OperationConfig(
            name="upload",
            type="action",
            fn="ingot.files.request_upload",
        )
        ctx = _operation_ctx(resource, op_config)

        from be.operations.action import Action

        opts = Action.Options(fn="ingot.files.request_upload")

        with patch(
            "be.operations.action.introspect_action_fn",
            return_value=info,
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.body_context["model_class_param_name"] == "model_cls"
        # Collection action: no PK in path.
        assert handler.path == "/upload"

    def test_action_status_code_overrides_default_204(self):
        """Override beats the ``-> None`` 204 default too."""
        from be.operations._introspect import IntrospectedAction

        resource = ResourceConfig(model="blog.models.Post")

        info = IntrospectedAction(
            model_param_name="post",
            model_class_param_name=None,
            request_class=None,
            request_module=None,
            response_class=None,
            response_module=None,
        )

        op_config = OperationConfig(
            name="reset",
            type="action",
            fn="blog.actions.reset",
        )
        ctx = _operation_ctx(resource, op_config)

        from be.operations.action import Action

        opts = Action.Options(fn="blog.actions.reset", status_code=205)

        with patch(
            "be.operations.action.introspect_action_fn",
            return_value=info,
        ):
            result = list(Action().build(ctx, opts))

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.status_code == 205
