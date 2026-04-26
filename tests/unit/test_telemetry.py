"""Tests for the telemetry config, scaffold op, and template wiring."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from foundry.engine import BuildContext
from foundry.outputs import StaticFile
from foundry.scope import PROJECT, Scope, ScopeTree
from foundry.store import BuildStore
from kiln.config.schema import (
    App,
    AppConfig,
    DatabaseConfig,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
    TelemetryConfig,
)
from kiln.operations.telemetry import TelemetryScaffold
from kiln.operations.types import RouteHandler

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestTelemetryConfigDefaults:
    def test_required_service_name(self):
        with pytest.raises(ValidationError, match="service_name"):
            TelemetryConfig()

    def test_sensible_defaults(self):
        cfg = TelemetryConfig(service_name="svc")
        assert cfg.traces is True
        assert cfg.metrics is True
        assert cfg.logs is False
        assert cfg.instrument_fastapi is True
        assert cfg.instrument_sqlalchemy is True
        assert cfg.instrument_httpx is False
        assert cfg.instrument_requests is False
        assert cfg.instrument_logging is False
        assert cfg.span_per_handler is True
        assert cfg.span_per_action is True
        assert cfg.capture_request_body is False
        assert cfg.capture_response_body is False
        assert cfg.sampler == "parentbased_always_on"
        assert cfg.sampler_ratio is None
        assert cfg.exporter is None
        assert cfg.environment_env == "ENVIRONMENT"
        assert cfg.resource_attributes == {}


class TestTelemetryConfigValidation:
    def test_ratio_required_for_traceidratio(self):
        with pytest.raises(ValidationError, match="sampler_ratio"):
            TelemetryConfig(service_name="svc", sampler="traceidratio")

    def test_ratio_required_for_parentbased_traceidratio(self):
        with pytest.raises(ValidationError, match="sampler_ratio"):
            TelemetryConfig(
                service_name="svc",
                sampler="parentbased_traceidratio",
            )

    def test_ratio_rejected_for_non_ratio_sampler(self):
        with pytest.raises(ValidationError, match="ratio sampler"):
            TelemetryConfig(
                service_name="svc",
                sampler="always_on",
                sampler_ratio=0.5,
            )

    def test_ratio_must_be_unit_interval(self):
        with pytest.raises(ValidationError, match=r"0\.0, 1\.0"):
            TelemetryConfig(
                service_name="svc",
                sampler="traceidratio",
                sampler_ratio=1.5,
            )

    def test_ratio_zero_accepted(self):
        cfg = TelemetryConfig(
            service_name="svc",
            sampler="traceidratio",
            sampler_ratio=0.0,
        )
        assert cfg.sampler_ratio == 0.0


class TestProjectConfigTelemetry:
    def test_telemetry_defaults_none(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        assert cfg.telemetry is None

    def test_telemetry_attached(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=TelemetryConfig(service_name="blog-api"),
        )
        assert cfg.telemetry is not None
        assert cfg.telemetry.service_name == "blog-api"

    def test_resource_trace_default_inherits(self):
        resource = ResourceConfig(model="blog.models.Article")
        assert resource.trace is None

    def test_op_trace_default_inherits(self):
        op = OperationConfig(name="get")
        assert op.trace is None


# ---------------------------------------------------------------------------
# TelemetryScaffold operation
# ---------------------------------------------------------------------------


SCOPE_TREE = ScopeTree([PROJECT])


def _project_ctx(config: ProjectConfig) -> BuildContext:
    return BuildContext(
        config=config,
        scope=PROJECT,
        instance=config,
        instance_id="project",
        store=BuildStore(scope_tree=SCOPE_TREE),
    )


class TestTelemetryScaffoldGate:
    def test_when_off_without_telemetry(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        op = TelemetryScaffold()
        assert op.when(_project_ctx(cfg)) is False

    def test_when_on_with_telemetry(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=TelemetryConfig(service_name="svc"),
        )
        op = TelemetryScaffold()
        assert op.when(_project_ctx(cfg)) is True


class TestTelemetryScaffoldOutputs:
    def test_emits_single_file(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=TelemetryConfig(service_name="svc"),
        )
        outputs = list(
            TelemetryScaffold().build(
                _project_ctx(cfg),
                _options=TelemetryScaffold().Options(),
            )
        )
        assert all(isinstance(o, StaticFile) for o in outputs)
        paths = {o.path for o in outputs}
        assert paths == {"telemetry.py"}

    def test_setup_context_carries_config_values(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=TelemetryConfig(
                service_name="svc",
                service_version="1.2.3",
                environment_env="DEPLOY_ENV",
                sampler="traceidratio",
                sampler_ratio=0.05,
                resource_attributes={"team": "platform"},
            ),
        )
        outputs = list(
            TelemetryScaffold().build(
                _project_ctx(cfg),
                _options=TelemetryScaffold().Options(),
            )
        )
        ctx = outputs[0].context
        assert ctx["service_name"] == "svc"
        assert ctx["service_version"] == "1.2.3"
        assert ctx["environment_env"] == "DEPLOY_ENV"
        assert ctx["sampler"] == "traceidratio"
        assert ctx["sampler_ratio"] == 0.05
        assert ctx["resource_attributes"] == {"team": "platform"}

    def test_telemetry_module_uses_package_prefix(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=TelemetryConfig(service_name="svc"),
        )
        outputs = list(
            TelemetryScaffold().build(
                _project_ctx(cfg),
                _options=TelemetryScaffold().Options(),
            )
        )
        assert outputs[0].context["telemetry_module"] == "_generated.telemetry"

    def test_telemetry_module_omits_empty_prefix(self):
        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            package_prefix="",
            telemetry=TelemetryConfig(service_name="svc"),
        )
        outputs = list(
            TelemetryScaffold().build(
                _project_ctx(cfg),
                _options=TelemetryScaffold().Options(),
            )
        )
        assert outputs[0].context["telemetry_module"] == "telemetry"


# ---------------------------------------------------------------------------
# Scaffold (db) wiring -- instrument_sqlalchemy passthrough
# ---------------------------------------------------------------------------


class TestDbScaffoldInstrumentFlag:
    def test_instrument_sqlalchemy_off_when_no_telemetry(self):
        from kiln.operations.scaffold import Scaffold

        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
        )
        outputs = list(
            Scaffold().build(_project_ctx(cfg), _options=Scaffold().Options())
        )
        session = next(o for o in outputs if o.path == "db/primary_session.py")
        assert session.context["instrument_sqlalchemy"] is False

    def test_instrument_sqlalchemy_on_with_telemetry(self):
        from kiln.operations.scaffold import Scaffold

        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=TelemetryConfig(service_name="svc"),
        )
        outputs = list(
            Scaffold().build(_project_ctx(cfg), _options=Scaffold().Options())
        )
        session = next(o for o in outputs if o.path == "db/primary_session.py")
        assert session.context["instrument_sqlalchemy"] is True

    def test_instrument_sqlalchemy_off_when_explicitly_disabled(self):
        from kiln.operations.scaffold import Scaffold

        cfg = ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=TelemetryConfig(
                service_name="svc",
                instrument_sqlalchemy=False,
            ),
        )
        outputs = list(
            Scaffold().build(_project_ctx(cfg), _options=Scaffold().Options())
        )
        session = next(o for o in outputs if o.path == "db/primary_session.py")
        assert session.context["instrument_sqlalchemy"] is False


# ---------------------------------------------------------------------------
# ProjectRouter wiring -- has_telemetry passthrough
# ---------------------------------------------------------------------------


class TestProjectRouterTelemetryFlag:
    def _config(self, telemetry: TelemetryConfig | None) -> ProjectConfig:
        return ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=telemetry,
            apps=[App(config=AppConfig(module="blog"), prefix="/blog")],
        )

    def test_has_telemetry_false_by_default(self):
        from kiln.operations.routing import ProjectRouter

        cfg = self._config(telemetry=None)
        outputs = list(
            ProjectRouter().build(
                _project_ctx(cfg), _options=ProjectRouter().Options()
            )
        )
        assert outputs[0].context["has_telemetry"] is False

    def test_has_telemetry_true_with_config(self):
        from kiln.operations.routing import ProjectRouter

        cfg = self._config(telemetry=TelemetryConfig(service_name="svc"))
        outputs = list(
            ProjectRouter().build(
                _project_ctx(cfg), _options=ProjectRouter().Options()
            )
        )
        ctx = outputs[0].context
        assert ctx["has_telemetry"] is True
        assert ctx["telemetry_module"] == "_generated.telemetry"


# ---------------------------------------------------------------------------
# AuthScaffold wiring -- has_telemetry on auth router context
# ---------------------------------------------------------------------------


class TestAuthScaffoldTelemetryFlag:
    def _config(self, telemetry: TelemetryConfig | None) -> ProjectConfig:
        from kiln.config.schema import AuthConfig

        return ProjectConfig(
            databases=[DatabaseConfig(key="primary", default=True)],
            telemetry=telemetry,
            auth=AuthConfig(
                credentials_schema="myapp.auth.LoginCredentials",
                session_schema="myapp.auth.Session",
                validate_fn="myapp.auth.validate",
            ),
        )

    def test_auth_router_carries_telemetry_flag(self):
        from kiln.operations.scaffold import AuthScaffold

        cfg = self._config(telemetry=TelemetryConfig(service_name="svc"))
        outputs = list(
            AuthScaffold().build(
                _project_ctx(cfg), _options=AuthScaffold().Options()
            )
        )
        router = next(o for o in outputs if o.path == "auth/router.py")
        # Auth router scrub imports straight from ``ingot.telemetry``
        # now -- no project-level decorators module to point at.
        assert router.context["has_telemetry"] is True

    def test_auth_router_no_telemetry_by_default(self):
        from kiln.operations.scaffold import AuthScaffold

        cfg = self._config(telemetry=None)
        outputs = list(
            AuthScaffold().build(
                _project_ctx(cfg), _options=AuthScaffold().Options()
            )
        )
        router = next(o for o in outputs if o.path == "auth/router.py")
        assert router.context["has_telemetry"] is False


# ---------------------------------------------------------------------------
# Tracing operation -- prepends @traced_handler to RouteHandlers in the
# build store.  Drives the op directly (resource scope,
# after_children=True) rather than going through the renderer, since
# the decoration is now a build-time concern not a render-time one.
# ---------------------------------------------------------------------------


from kiln.operations.tracing import Tracing  # noqa: E402


def _crud_handler(op_name: str = "get") -> RouteHandler:
    return RouteHandler(
        method="GET",
        path="/{id}",
        function_name=f"{op_name}_post",
        op_name=op_name,
    )


def _action_handler(op_name: str = "publish") -> RouteHandler:
    return RouteHandler(
        method="POST",
        path=f"/{{id}}/{op_name}",
        function_name=f"{op_name}_post",
        op_name=op_name,
        body_template="fastapi/ops/action.py.j2",
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
    telemetry: TelemetryConfig | None,
    resource: ResourceConfig,
    handlers: list[RouteHandler],
) -> BuildContext:
    """Build a resource-scope BuildContext with handlers registered.

    Mirrors how the engine sets things up: a project carries the
    telemetry config; the resource scope holds OperationConfig
    children; each child's outputs include RouteHandlers.
    """
    cfg = ProjectConfig(
        databases=[DatabaseConfig(key="primary", default=True)],
        telemetry=telemetry,
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
        store.add(op_id, "tracing-test", handler)
    return BuildContext(
        config=cfg,
        scope=_RESOURCE_SCOPE,
        instance=resource,
        instance_id=resource_id,
        store=store,
    )


def _run_tracing(
    *,
    telemetry: TelemetryConfig | None,
    resource: ResourceConfig | None = None,
    handlers: list[RouteHandler] | None = None,
) -> list[RouteHandler]:
    """Run the Tracing op against a single resource and return the handlers."""
    handlers = handlers if handlers is not None else [_crud_handler()]
    op_configs = [
        OperationConfig(name=h.op_name)
        for h in handlers
        if h.body_template != "fastapi/ops/action.py.j2"
    ] + [
        OperationConfig(name=h.op_name, type="action")
        for h in handlers
        if h.body_template == "fastapi/ops/action.py.j2"
    ]
    resource = resource or ResourceConfig(
        model="myapp.models.Post", operations=op_configs
    )
    ctx = _resource_ctx(
        telemetry=telemetry, resource=resource, handlers=handlers
    )
    op = Tracing()
    if op.when(ctx):
        list(op.build(ctx, _options=Tracing.Options()))
    return handlers


class TestTracingOp:
    def test_when_returns_false_without_telemetry(self):
        ctx = _resource_ctx(
            telemetry=None,
            resource=ResourceConfig(model="myapp.models.Post"),
            handlers=[],
        )
        assert Tracing().when(ctx) is False

    def test_when_returns_true_with_telemetry(self):
        ctx = _resource_ctx(
            telemetry=TelemetryConfig(service_name="svc"),
            resource=ResourceConfig(model="myapp.models.Post"),
            handlers=[],
        )
        assert Tracing().when(ctx) is True

    def test_no_decorator_when_telemetry_off(self):
        handlers = _run_tracing(telemetry=None)
        assert handlers[0].decorators == []
        assert handlers[0].extra_imports == []

    def test_traced_handler_added_for_crud(self):
        handlers = _run_tracing(
            telemetry=TelemetryConfig(service_name="svc"),
        )
        decs = handlers[0].decorators
        assert len(decs) == 1
        assert decs[0].startswith("@traced_handler(")
        assert '"post.get"' in decs[0]
        assert 'resource="post"' in decs[0]
        assert 'op="get"' in decs[0]
        # Exception recording is *not* a knob -- the surrounding
        # FastAPIInstrumentor server span is the authoritative
        # success/failure signal.
        assert "record_exceptions" not in decs[0]
        assert (
            "ingot.telemetry",
            "traced_handler",
        ) in handlers[0].extra_imports

    def test_traced_handler_emitted_for_action(self):
        # Actions reuse traced_handler; the user-defined ``op`` name
        # discriminates from the fixed CRUD name set.
        handlers = _run_tracing(
            telemetry=TelemetryConfig(service_name="svc"),
            handlers=[_action_handler("publish")],
        )
        decs = handlers[0].decorators
        assert len(decs) == 1
        assert decs[0].startswith("@traced_handler(")
        assert 'op="publish"' in decs[0]
        assert "action=" not in decs[0]

    def test_resource_trace_false_skips_decorator(self):
        handlers = _run_tracing(
            telemetry=TelemetryConfig(service_name="svc"),
            resource=ResourceConfig(
                model="myapp.models.Post",
                trace=False,
                operations=[OperationConfig(name="get")],
            ),
        )
        assert handlers[0].decorators == []

    def test_op_trace_false_skips_only_that_op(self):
        # Per-op opt-out leaves siblings untouched.
        handlers = _run_tracing(
            telemetry=TelemetryConfig(service_name="svc"),
            resource=ResourceConfig(
                model="myapp.models.Post",
                operations=[
                    OperationConfig(name="get", trace=False),
                    OperationConfig(name="list"),
                ],
            ),
            handlers=[_crud_handler("get"), _crud_handler("list")],
        )
        assert handlers[0].decorators == []  # get: opted out
        assert handlers[1].decorators != []  # list: still traced

    def test_span_per_handler_off_skips_crud(self):
        handlers = _run_tracing(
            telemetry=TelemetryConfig(
                service_name="svc", span_per_handler=False
            ),
        )
        assert handlers[0].decorators == []

    def test_span_per_action_off_skips_actions(self):
        handlers = _run_tracing(
            telemetry=TelemetryConfig(
                service_name="svc", span_per_action=False
            ),
            handlers=[_action_handler("publish")],
        )
        assert handlers[0].decorators == []

    def test_span_per_handler_off_does_not_disable_actions(self):
        # span_per_handler and span_per_action are independent gates.
        handlers = _run_tracing(
            telemetry=TelemetryConfig(
                service_name="svc",
                span_per_handler=False,
                span_per_action=True,
            ),
            handlers=[_action_handler("publish")],
        )
        assert handlers[0].decorators[0].startswith("@traced_handler(")
