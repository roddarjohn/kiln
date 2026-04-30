"""Tests for the rate-limit config, scaffold op, and decorator op."""

import datetime as dt
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import DeclarativeBase

from be.config.schema import (
    App,
    AppConfig,
    DatabaseConfig,
    OperationConfig,
    ProjectConfig,
    RateLimitConfig,
    ResourceConfig,
)
from be.operations.rate_limit import RateLimit
from be.operations.rate_limit_scaffold import RateLimitScaffold
from be.operations.types import RouteHandler, RouteParam
from foundry.engine import BuildContext
from foundry.outputs import StaticFile
from foundry.scope import PROJECT, Scope, ScopeTree
from foundry.store import BuildStore
from ingot.rate_limit import (
    PostgresStorage,
    RateLimitBucketMixin,
    build_limiter,
    default_key_func,
)
from ingot.utils import compile_query

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestRateLimitConfigDefaults:
    def test_required_bucket_model(self):
        with pytest.raises(ValidationError, match="bucket_model"):
            RateLimitConfig()

    def test_sensible_defaults(self):
        cfg = RateLimitConfig(bucket_model="myapp.models.RateLimitBucket")
        # 60/minute = one hit per second on average -- conservative
        # enough to block rapid abuse without breaking real clients.
        assert cfg.default_limit == "60/minute"
        assert cfg.key_func is None
        assert cfg.db_key is None
        assert cfg.headers_enabled is True

    def test_default_limit_can_be_disabled_explicitly(self):
        cfg = RateLimitConfig(
            bucket_model="myapp.models.RateLimitBucket",
            default_limit=None,
        )
        assert cfg.default_limit is None


class TestProjectConfigRateLimit:
    def test_rate_limit_defaults_none(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        assert cfg.rate_limit is None

    def test_rate_limit_attached(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket",
                default_limit="100/minute",
            ),
        )
        assert cfg.rate_limit is not None
        assert cfg.rate_limit.default_limit == "100/minute"

    def test_resource_rate_limit_default_inherits(self):
        resource = ResourceConfig(model="myapp.models.Post")
        assert resource.rate_limit is None

    def test_op_rate_limit_default_inherits(self):
        op = OperationConfig(name="get")
        assert op.rate_limit is None


class TestRateLimitOverrideValidation:
    def _config_with(
        self,
        *,
        project_rl: RateLimitConfig | None,
        resource_rl: str | bool | None = None,
        op_rl: str | bool | None = None,
    ) -> dict:
        return {
            "databases": [DatabaseConfig(key="primary", default=True)],
            "rate_limit": project_rl,
            "apps": [
                App(
                    config=AppConfig(
                        module="myapp",
                        resources=[
                            ResourceConfig(
                                model="myapp.models.Post",
                                rate_limit=resource_rl,
                                operations=[
                                    OperationConfig(
                                        name="get", rate_limit=op_rl
                                    )
                                ],
                            )
                        ],
                    )
                )
            ],
        }

    def test_resource_override_requires_project_config(self):
        with pytest.raises(ValidationError, match="project has no rate_limit"):
            ProjectConfig(
                **self._config_with(
                    project_rl=None,
                    resource_rl="100/minute",
                )
            )

    def test_op_override_requires_project_config(self):
        with pytest.raises(ValidationError, match="project has no rate_limit"):
            ProjectConfig(
                **self._config_with(
                    project_rl=None,
                    op_rl="5/second",
                )
            )

    def test_op_false_override_requires_project_config(self):
        # False is a valid override but still requires project config
        # since otherwise there's nothing to disable.
        with pytest.raises(ValidationError, match="project has no rate_limit"):
            ProjectConfig(
                **self._config_with(
                    project_rl=None,
                    op_rl=False,
                )
            )

    def test_overrides_accepted_with_project_config(self):
        cfg = ProjectConfig(
            **self._config_with(
                project_rl=RateLimitConfig(
                    bucket_model="myapp.models.RateLimitBucket",
                    default_limit="100/minute",
                ),
                resource_rl="50/minute",
                op_rl="5/second",
            )
        )
        assert cfg.rate_limit is not None

    def test_db_key_must_resolve(self):
        with pytest.raises(ValidationError, match="missing"):
            ProjectConfig(
                databases=[DatabaseConfig(key="primary", default=True)],
                rate_limit=RateLimitConfig(
                    bucket_model="myapp.models.RateLimitBucket",
                    db_key="missing",
                ),
            )

    def test_db_key_default_resolves_to_default_db(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket",
            ),
        )
        # The validator picks the default-flagged DB when db_key is None.
        assert cfg.rate_limit is not None


# ---------------------------------------------------------------------------
# RateLimitScaffold operation
# ---------------------------------------------------------------------------


_SCOPE_TREE_PROJECT = ScopeTree([PROJECT])


def _project_ctx(config: ProjectConfig) -> BuildContext:
    return BuildContext(
        config=config,
        scope=PROJECT,
        instance=config,
        instance_id="project",
        store=BuildStore(scope_tree=_SCOPE_TREE_PROJECT),
    )


class TestRateLimitScaffoldGate:
    def test_when_off_without_rate_limit(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        assert RateLimitScaffold().when(_project_ctx(cfg)) is False

    def test_when_on_with_rate_limit(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket",
            ),
        )
        assert RateLimitScaffold().when(_project_ctx(cfg)) is True


class TestRateLimitScaffoldOutputs:
    def _build(self, cfg: ProjectConfig) -> list[StaticFile]:
        return list(
            RateLimitScaffold().build(
                _project_ctx(cfg), _options=RateLimitScaffold().Options()
            )
        )

    def _cfg(self, **rl_kwargs) -> ProjectConfig:
        return ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket", **rl_kwargs
            ),
        )

    def test_emits_single_rate_limit_file(self):
        outputs = self._build(self._cfg())
        assert all(isinstance(o, StaticFile) for o in outputs)
        assert {o.path for o in outputs} == {"rate_limit.py"}

    def test_bucket_module_and_class_split(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["bucket_module"] == "myapp.models"
        assert ctx["bucket_class"] == "RateLimitBucket"

    def test_default_key_func_when_unset(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["key_func_module"] == "ingot.rate_limit"
        assert ctx["key_func_name"] == "default_key_func"

    def test_custom_key_func(self):
        ctx = self._build(self._cfg(key_func="myapp.security.real_ip"))[
            0
        ].context
        assert ctx["key_func_module"] == "myapp.security"
        assert ctx["key_func_name"] == "real_ip"

    def test_default_limit_passthrough(self):
        ctx = self._build(self._cfg(default_limit="200/minute"))[0].context
        assert ctx["default_limit"] == "200/minute"

    def test_default_limit_uses_60_per_minute_when_unset(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["default_limit"] == "60/minute"

    def test_default_limit_can_be_explicitly_disabled(self):
        ctx = self._build(self._cfg(default_limit=None))[0].context
        assert ctx["default_limit"] is None

    def test_headers_enabled_passthrough(self):
        ctx = self._build(self._cfg(headers_enabled=False))[0].context
        assert ctx["headers_enabled"] is False

    def test_url_env_from_default_database(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["url_env"] == "DATABASE_URL"
        assert ctx["db_key"] == "primary"

    def test_url_env_from_named_database(self):
        cfg = ProjectConfig(
            databases=[
                DatabaseConfig(key="primary", default=True),
                DatabaseConfig(key="audit", url_env="AUDIT_DB_URL"),
            ],
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket",
                db_key="audit",
            ),
        )
        ctx = self._build(cfg)[0].context
        assert ctx["url_env"] == "AUDIT_DB_URL"
        assert ctx["db_key"] == "audit"

    def test_rate_limit_module_uses_package_prefix(self):
        ctx = self._build(self._cfg())[0].context
        assert ctx["rate_limit_module"] == "_generated.rate_limit"

    def test_rate_limit_module_omits_empty_prefix(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            package_prefix="",
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket",
            ),
        )
        ctx = self._build(cfg)[0].context
        assert ctx["rate_limit_module"] == "rate_limit"


# ---------------------------------------------------------------------------
# RateLimit decorator-injection operation
# ---------------------------------------------------------------------------


def _crud_handler(op_name: str = "get") -> RouteHandler:
    return RouteHandler(
        method="GET",
        path="/{id}",
        function_name=f"{op_name}_post",
        op_name=op_name,
    )


_APP_SCOPE = Scope(name="app", config_key="apps", parent=PROJECT)
_RESOURCE_SCOPE = Scope(
    name="resource", config_key="resources", parent=_APP_SCOPE
)
_OP_SCOPE = Scope(
    name="operation", config_key="operations", parent=_RESOURCE_SCOPE
)
_SCOPE_TREE = ScopeTree([PROJECT, _APP_SCOPE, _RESOURCE_SCOPE, _OP_SCOPE])


def _resource_ctx(
    *,
    rate_limit: RateLimitConfig | None,
    resource: ResourceConfig,
    handlers: list[RouteHandler],
) -> BuildContext:
    """Build a resource-scope context with handlers staged in the store."""
    cfg = ProjectConfig(
        databases=[DatabaseConfig(key="primary", default=True)],
        rate_limit=rate_limit,
        apps=[
            App(
                config=AppConfig(module="myapp", resources=[resource]),
                prefix="",
            )
        ],
    )
    store = BuildStore(scope_tree=_SCOPE_TREE)
    store.register_instance("project", cfg)
    store.register_instance("project.apps.0", cfg.apps[0], parent="project")
    resource_id = "project.apps.0.resources.0"
    store.register_instance(resource_id, resource, parent="project.apps.0")

    for idx, handler in enumerate(handlers):
        op_id = f"{resource_id}.operations.{idx}"
        op_cfg = next(
            (op for op in resource.operations if op.name == handler.op_name),
            OperationConfig(name=handler.op_name),
        )
        store.register_instance(op_id, op_cfg, parent=resource_id)
        store.add(op_id, "rate-limit-test", handler)

    return BuildContext(
        config=cfg,
        scope=_RESOURCE_SCOPE,
        instance=resource,
        instance_id=resource_id,
        store=store,
        package_prefix="_generated",
    )


def _run(
    *,
    rate_limit: RateLimitConfig | None,
    resource: ResourceConfig | None = None,
    handlers: list[RouteHandler] | None = None,
) -> list[RouteHandler]:
    handlers = handlers if handlers is not None else [_crud_handler()]
    op_configs = [OperationConfig(name=h.op_name) for h in handlers]
    resource = resource or ResourceConfig(
        model="myapp.models.Post", operations=op_configs
    )
    ctx = _resource_ctx(
        rate_limit=rate_limit, resource=resource, handlers=handlers
    )
    op = RateLimit()

    if op.when(ctx):
        list(op.build(ctx, _options=RateLimit.Options()))

    return handlers


_PROJECT_RL = RateLimitConfig(
    bucket_model="myapp.models.RateLimitBucket",
    default_limit="100/minute",
)


class TestRateLimitOpGate:
    def test_when_returns_false_without_project_config(self):
        ctx = _resource_ctx(
            rate_limit=None,
            resource=ResourceConfig(model="myapp.models.Post"),
            handlers=[],
        )
        assert RateLimit().when(ctx) is False

    def test_when_returns_true_with_project_config(self):
        ctx = _resource_ctx(
            rate_limit=_PROJECT_RL,
            resource=ResourceConfig(model="myapp.models.Post"),
            handlers=[],
        )
        assert RateLimit().when(ctx) is True


class TestRateLimitDecoration:
    def test_no_decorator_when_no_config(self):
        handlers = _run(rate_limit=None)
        assert handlers[0].decorators == []
        assert handlers[0].extra_imports == []

    def test_default_limit_decorates_all_handlers(self):
        handlers = _run(rate_limit=_PROJECT_RL)
        assert handlers[0].decorators == ['@limiter.limit("100/minute")']

    def test_no_default_no_overrides_means_no_decorator(self):
        # Explicitly opt out of the project-wide default by passing
        # ``default_limit=None``; with no resource/op overrides
        # there's nothing left to apply.
        handlers = _run(
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket",
                default_limit=None,
            ),
        )
        assert handlers[0].decorators == []

    def test_resource_string_overrides_project_default(self):
        resource = ResourceConfig(
            model="myapp.models.Post",
            rate_limit="20/minute",
            operations=[OperationConfig(name="get")],
        )
        handlers = _run(rate_limit=_PROJECT_RL, resource=resource)
        assert handlers[0].decorators == ['@limiter.limit("20/minute")']

    def test_resource_false_short_circuits_inheriting_ops(self):
        # Resource-level ``False`` kills ops that inherit (None);
        # ops that explicitly override with a value win the
        # cascade and are still decorated.
        resource = ResourceConfig(
            model="myapp.models.Post",
            rate_limit=False,
            operations=[
                OperationConfig(name="get"),
                OperationConfig(name="list", rate_limit="5/minute"),
            ],
        )
        handlers = _run(
            rate_limit=_PROJECT_RL,
            resource=resource,
            handlers=[_crud_handler("get"), _crud_handler("list")],
        )

        # ``get`` inherits → resource-level False kills it.
        get = next(h for h in handlers if h.op_name == "get")
        assert get.decorators == []
        # ``list`` overrides explicitly → cascade picks it up.
        lst = next(h for h in handlers if h.op_name == "list")
        assert lst.decorators == ['@limiter.limit("5/minute")']

    def test_op_string_overrides_resource_and_project(self):
        resource = ResourceConfig(
            model="myapp.models.Post",
            rate_limit="20/minute",
            operations=[OperationConfig(name="get", rate_limit="2/second")],
        )
        handlers = _run(rate_limit=_PROJECT_RL, resource=resource)
        assert handlers[0].decorators == ['@limiter.limit("2/second")']

    def test_op_false_skips_decoration(self):
        resource = ResourceConfig(
            model="myapp.models.Post",
            operations=[OperationConfig(name="get", rate_limit=False)],
        )
        handlers = _run(rate_limit=_PROJECT_RL, resource=resource)
        assert handlers[0].decorators == []

    def test_request_param_injected(self):
        handlers = _run(rate_limit=_PROJECT_RL)
        names = [p.name for p in handlers[0].params]
        assert names == ["request"]
        assert handlers[0].params[0].annotation == "Request"

    def test_request_param_not_duplicated(self):
        # ``RouteHandler.add_param`` enforces uniqueness at insert
        # time -- when a handler already declares ``request``, the
        # rate-limit op's add is a no-op.
        h = _crud_handler()
        h.params.append(RouteParam(name="request", annotation="Request"))
        handlers = _run(rate_limit=_PROJECT_RL, handlers=[h])
        request_params = [p for p in handlers[0].params if p.name == "request"]
        assert len(request_params) == 1

    def test_imports_added(self):
        handlers = _run(rate_limit=_PROJECT_RL)
        assert ("_generated.rate_limit", "limiter") in handlers[0].extra_imports
        assert ("fastapi", "Request") in handlers[0].extra_imports

    def test_op_with_no_default_skips_decoration(self):
        # When the project explicitly opts out of the default
        # (``default_limit=None``) and no per-resource/op override
        # is set, nothing should be decorated.
        resource = ResourceConfig(
            model="myapp.models.Post",
            operations=[OperationConfig(name="get")],
        )
        handlers = _run(
            rate_limit=RateLimitConfig(
                bucket_model="myapp.models.RateLimitBucket",
                default_limit=None,
            ),
            resource=resource,
        )
        assert handlers[0].decorators == []


# ---------------------------------------------------------------------------
# PostgresStorage SQL shape
# ---------------------------------------------------------------------------


class _Base(DeclarativeBase):
    pass


class _Bucket(_Base, RateLimitBucketMixin):
    __tablename__ = "rate_limit_buckets_test"


class TestPostgresStorageSql:
    def _storage(
        self,
    ) -> tuple[PostgresStorage, MagicMock, MagicMock]:
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        maker = MagicMock(return_value=session)
        storage = PostgresStorage(model=_Bucket, session_maker=maker)
        return storage, maker, session

    def test_incr_runs_postgres_upsert(self):
        storage, _, session = self._storage()
        session.execute.return_value.scalar_one.return_value = 7

        result = storage.incr("k", 60, amount=1)

        assert result == 7
        # Inspect the SQL passed to ``session.execute``.
        stmt = session.execute.call_args[0][0]
        sql = compile_query(stmt, dialect="postgres")
        assert "INSERT INTO rate_limit_buckets_test" in sql
        assert "ON CONFLICT" in sql
        assert "RETURNING" in sql

    def test_get_returns_zero_when_missing(self):
        storage, _, session = self._storage()
        session.execute.return_value.first.return_value = None
        assert storage.get("k") == 0

    def test_get_returns_zero_when_stale(self):
        storage, _, session = self._storage()
        past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        session.execute.return_value.first.return_value = (5, past)
        assert storage.get("k") == 0

    def test_get_returns_hits_when_active(self):
        storage, _, session = self._storage()
        future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
        session.execute.return_value.first.return_value = (5, future)
        assert storage.get("k") == 5

    def test_clear_issues_delete(self):
        storage, _, session = self._storage()
        storage.clear("k")
        stmt = session.execute.call_args[0][0]
        sql = compile_query(stmt, dialect="postgres")
        assert "DELETE FROM rate_limit_buckets_test" in sql
        assert "WHERE" in sql
        session.commit.assert_called_once()

    def test_reset_issues_unfiltered_delete(self):
        storage, _, session = self._storage()
        session.execute.return_value.rowcount = 3
        result = storage.reset()
        assert result == 3
        stmt = session.execute.call_args[0][0]
        sql = compile_query(stmt, dialect="postgres")
        assert "DELETE FROM rate_limit_buckets_test" in sql
        # No WHERE clause -- reset wipes the whole table.
        assert "WHERE" not in sql

    def test_base_exceptions_is_sqlalchemy_error(self):
        from sqlalchemy.exc import SQLAlchemyError

        storage, _, _ = self._storage()
        assert storage.base_exceptions is SQLAlchemyError


class TestPostgresStorageGetExpiry:
    def _storage(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        maker = MagicMock(return_value=session)
        return PostgresStorage(model=_Bucket, session_maker=maker), session

    def test_returns_now_when_row_missing(self):
        storage, session = self._storage()
        session.execute.return_value.scalar_one_or_none.return_value = None

        before = dt.datetime.now(dt.UTC).timestamp()
        result = storage.get_expiry("k")
        after = dt.datetime.now(dt.UTC).timestamp()

        # Missing row: returns "now" so ``limits`` treats the
        # window as already expired.
        assert before <= result <= after

    def test_returns_row_expiry_as_unix_timestamp(self):
        storage, session = self._storage()
        future = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
        session.execute.return_value.scalar_one_or_none.return_value = future

        assert storage.get_expiry("k") == future.timestamp()


class TestPostgresStorageCheck:
    def _storage_with(self, *, raises=False):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        if raises:
            from sqlalchemy.exc import OperationalError

            session.execute.side_effect = OperationalError("x", {}, None)

        maker = MagicMock(return_value=session)
        return PostgresStorage(model=_Bucket, session_maker=maker)

    def test_returns_true_on_healthy_db(self):
        assert self._storage_with(raises=False).check() is True

    def test_returns_false_on_sqlalchemy_error(self):
        assert self._storage_with(raises=True).check() is False


class TestBuildLimiter:
    def test_wires_postgres_storage(self):
        engine = MagicMock()
        limiter = build_limiter(
            model=_Bucket,
            sync_url="postgresql://unused",
            engine=engine,
        )
        # Replaced storage and strategy.
        assert isinstance(limiter._storage, PostgresStorage)
        # ``_limiter`` is rebuilt with a fixed-window strategy.
        from limits.strategies import FixedWindowRateLimiter

        assert isinstance(limiter._limiter, FixedWindowRateLimiter)

    def test_default_limits_passed_through(self):
        engine = MagicMock()
        limiter = build_limiter(
            model=_Bucket,
            sync_url="postgresql://unused",
            engine=engine,
            default_limits=("5/minute",),
        )
        assert len(limiter._default_limits) == 1

    def test_default_key_func_used_when_unset(self):
        engine = MagicMock()
        limiter = build_limiter(
            model=_Bucket, sync_url="postgresql://unused", engine=engine
        )
        assert limiter._key_func is default_key_func

    def test_custom_key_func_honored(self):
        engine = MagicMock()
        custom = MagicMock(return_value="x")
        limiter = build_limiter(
            model=_Bucket,
            sync_url="postgresql://unused",
            engine=engine,
            key_func=custom,
        )
        assert limiter._key_func is custom

    def test_builds_engine_from_sync_url_when_unset(self, monkeypatch):
        # The production path: when ``engine`` is omitted,
        # build_limiter calls ``create_engine`` with the DSN.
        fake_engine = MagicMock()
        calls: list[tuple[str, dict]] = []

        def fake_create_engine(url, **kwargs):
            calls.append((url, kwargs))
            return fake_engine

        monkeypatch.setattr(
            "ingot.rate_limit.create_engine", fake_create_engine
        )
        build_limiter(model=_Bucket, sync_url="postgresql://localhost/x")
        assert calls == [
            (
                "postgresql://localhost/x",
                {"future": True, "pool_pre_ping": True},
            )
        ]


class TestDefaultKeyFunc:
    def test_returns_client_host(self):
        request = MagicMock()
        request.client.host = "1.2.3.4"
        assert default_key_func(request) == "1.2.3.4"

    def test_falls_back_to_unknown_when_no_client(self):
        request = MagicMock()
        request.client = None
        assert default_key_func(request) == "unknown"
