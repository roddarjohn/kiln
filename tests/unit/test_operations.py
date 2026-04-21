"""Tests for kiln.operations — scaffold, routing, crud, action."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from pydantic import BaseModel

from foundry.engine import BuildContext
from foundry.outputs import (
    Field,
    RouteHandler,
    RouterMount,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.render import BuildStore
from foundry.scope import PROJECT, Scope
from kiln.config.schema import (
    AppRef,
    AuthConfig,
    DatabaseConfig,
    FieldSpec,
    KilnConfig,
    ResourceConfig,
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


RESOURCE_SCOPE = Scope(
    name="resource",
    config_key="resources",
    parent=PROJECT,
)

APP_SCOPE = Scope(
    name="app",
    config_key="apps",
    parent=PROJECT,
)


def _resource_ctx(
    resource: ResourceConfig,
    *,
    config: MinimalConfig | None = None,
    store: BuildStore | None = None,
) -> BuildContext:
    """Build a BuildContext for a resource operation."""
    cfg = config or MinimalConfig()
    return BuildContext(
        config=cfg,
        scope=RESOURCE_SCOPE,
        instance=resource,
        instance_id=resource.model.rpartition(".")[2].lower(),
        store=store or BuildStore(),
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

    def test_default_db_session(self):
        """No databases → default session.py."""
        ctx = _project_ctx()
        result = list(Scaffold().build(ctx, _Empty()))
        paths = [f.path for f in result]
        assert "db/__init__.py" in paths
        assert "db/session.py" in paths

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

    def test_db_session_context(self):
        """Default session has expected context values."""
        ctx = _project_ctx()
        result = list(Scaffold().build(ctx, _Empty()))
        session = next(f for f in result if f.path == "db/session.py")
        assert session.context["url_env"] == "DATABASE_URL"
        assert session.context["get_db_fn"] == "get_db"
        assert session.context["pool_pre_ping"] is True

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


class TestRouter:
    """Tests for Router operation."""

    @staticmethod
    def _res(name: str) -> ResourceConfig:
        """A ResourceConfig whose iid will be *name* lowercase."""
        return ResourceConfig(model=f"pkg.models.{name.capitalize()}")

    @staticmethod
    def _ctx(
        module: str,
        resources: list[ResourceConfig],
        store: BuildStore,
    ) -> BuildContext:
        config = KilnConfig(module=module, resources=resources)
        return BuildContext(
            config=config,
            scope=PROJECT,
            instance=config,
            instance_id="project",
            store=store,
        )

    @staticmethod
    def _multi_app_ctx(
        apps: list[tuple[str, list[ResourceConfig]]],
        store: BuildStore,
    ) -> BuildContext:
        """Context for a multi-app config.

        Args:
            apps: ``(module, resources)`` per app.
            store: Build store to attach.
        """
        config = KilnConfig(
            module="project",
            apps=[
                AppRef(
                    config=KilnConfig(module=module, resources=resources),
                    prefix=f"/{module}",
                )
                for module, resources in apps
            ],
        )
        return BuildContext(
            config=config,
            scope=PROJECT,
            instance=config,
            instance_id="project",
            store=store,
        )

    @staticmethod
    def _add_handler(store: BuildStore, iid: str) -> None:
        store.add(
            "resource",
            iid,
            "get",
            RouteHandler(
                method="GET",
                path="/{id}",
                function_name=f"get_{iid}",
            ),
        )

    def test_mounts_resources_from_store(self):
        """One RouterMount per resource with a RouteHandler in the store."""
        store = BuildStore()
        self._add_handler(store, "post")
        self._add_handler(store, "comment")
        ctx = self._ctx("blog", [self._res("Post"), self._res("Comment")], store)

        result = list(Router().build(ctx, _Empty()))
        mounts = [r for r in result if isinstance(r, RouterMount)]
        statics = [r for r in result if isinstance(r, StaticFile)]

        assert len(mounts) == 2
        assert mounts[0].module == "blog.routes.post"
        assert mounts[0].alias == "post_router"
        assert mounts[1].module == "blog.routes.comment"
        assert mounts[1].alias == "comment_router"
        assert len(statics) == 1
        assert statics[0].path == "blog/routes/__init__.py"

    def test_router_static_context(self):
        """Static file context has correct route entries."""
        store = BuildStore()
        self._add_handler(store, "user")
        ctx = self._ctx("api", [self._res("User")], store)

        result = list(Router().build(ctx, _Empty()))
        static = next(r for r in result if isinstance(r, StaticFile))
        routes = static.context["routes"]
        assert len(routes) == 1
        assert routes[0]["module_name"] == "user"
        assert routes[0]["alias"] == "user_router"

    def test_deduplicates_iid_across_ops(self):
        """One resource with multiple route-emitting ops mounts once."""
        store = BuildStore()
        store.add(
            "resource",
            "user",
            "get",
            RouteHandler(method="GET", path="/{id}", function_name="get_user"),
        )
        store.add(
            "resource",
            "user",
            "list",
            RouteHandler(method="GET", path="/", function_name="list_user"),
        )
        ctx = self._ctx("api", [self._res("User")], store)

        result = list(Router().build(ctx, _Empty()))
        mounts = [r for r in result if isinstance(r, RouterMount)]
        assert len(mounts) == 1
        assert mounts[0].alias == "user_router"

    def test_skips_resources_without_handlers(self):
        """A resource with no RouteHandler entries is not mounted."""
        store = BuildStore()
        store.add(
            "resource",
            "silent",
            "some_op",
            StaticFile(path="silent.py", template="x.j2"),
        )
        self._add_handler(store, "loud")
        ctx = self._ctx("api", [self._res("Silent"), self._res("Loud")], store)

        result = list(Router().build(ctx, _Empty()))
        mounts = [r for r in result if isinstance(r, RouterMount)]
        aliases = [m.alias for m in mounts]
        assert aliases == ["loud_router"]

    def test_ignores_non_resource_scope(self):
        """RouteHandlers outside resource scope are not mounted."""
        store = BuildStore()
        store.add(
            "project",
            "project",
            "whatever",
            RouteHandler(method="GET", path="/", function_name="root"),
        )
        ctx = self._ctx("api", [self._res("User")], store)

        result = list(Router().build(ctx, _Empty()))
        assert result == []

    def test_no_handlers_returns_empty(self):
        """Empty store → no output."""
        ctx = self._ctx("app", [self._res("User")], BuildStore())
        result = list(Router().build(ctx, _Empty()))
        assert result == []

    def test_multi_app_emits_per_app_routers(self):
        """Multi-app config produces one routes/__init__.py per app."""
        store = BuildStore()
        self._add_handler(store, "post")
        self._add_handler(store, "product")
        ctx = self._multi_app_ctx(
            [
                ("blog", [self._res("Post")]),
                ("shop", [self._res("Product")]),
            ],
            store,
        )

        result = list(Router().build(ctx, _Empty()))
        statics = [r for r in result if isinstance(r, StaticFile)]
        paths = {s.path for s in statics}
        assert paths == {"blog/routes/__init__.py", "shop/routes/__init__.py"}


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
        app_config = KilnConfig(module="blog")
        config = MinimalConfig(
            apps=[AppRef(config=app_config, prefix="/blog")],
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
        app_config = KilnConfig(module="blog")
        config = MinimalConfig(
            auth=AuthConfig(
                verify_credentials_fn="myapp.verify",
            ),
            apps=[AppRef(config=app_config, prefix="/blog")],
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
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))

        schemas = [r for r in result if isinstance(r, SchemaClass)]
        assert len(schemas) == 1
        assert schemas[0].name == "UserListItem"

        sers = [r for r in result if isinstance(r, SerializerFn)]
        assert len(sers) == 1
        assert sers[0].function_name == "to_user_list_item"
        assert sers[0].schema_name == "UserListItem"

        handler = next(r for r in result if isinstance(r, RouteHandler))
        assert handler.method == "GET"
        assert handler.path == "/"
        assert handler.function_name == "list_users"
        assert handler.response_model == "list[UserListItem]"
        assert handler.return_type == "list[UserListItem]"
        assert handler.serializer_fn == "to_user_list_item"

    def test_list_test_case(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _resource_ctx(resource)
        result = list(List().build(ctx, List.Options(fields=_FIELDS)))
        tests = [r for r in result if isinstance(r, TestCase)]
        assert tests[0].op_name == "list"
        assert tests[0].is_list_response is True


# -------------------------------------------------------------------
# Create
# -------------------------------------------------------------------


class TestCreate:
    """Tests for Create operation."""

    def test_create_emits_schema_and_handler(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
        result = list(Delete().build(ctx, _Empty()))

        handlers = [r for r in result if isinstance(r, RouteHandler)]
        assert len(handlers) == 1
        assert handlers[0].method == "DELETE"
        assert handlers[0].status_code == 204
        assert handlers[0].function_name == "delete_user"

    def test_delete_test_case(self):
        resource = ResourceConfig(model="app.models.User")
        ctx = _resource_ctx(resource)
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
        ctx = _resource_ctx(resource)
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

        ctx = _resource_ctx(resource)
        ctx = BuildContext(
            config=ctx.config,
            scope=ctx.scope,
            instance=resource,
            instance_id="publish",
            store=ctx.store,
        )

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

        ctx = BuildContext(
            config=MinimalConfig(),
            scope=RESOURCE_SCOPE,
            instance=resource,
            instance_id="bulk_import",
            store=BuildStore(),
        )

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
