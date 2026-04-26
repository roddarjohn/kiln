"""Tests for the telemetry config, scaffold op, and template wiring."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from foundry.engine import BuildContext
from foundry.outputs import StaticFile
from foundry.render import RenderCtx
from foundry.render import registry as shared_registry
from foundry.scope import PROJECT, ScopeTree, discover_scopes
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
        assert cfg.instrument_logging is False
        assert cfg.span_per_handler is True
        assert cfg.span_per_action is True
        assert cfg.record_exceptions is True
        assert cfg.capture_request_body is False
        assert cfg.capture_response_body is False
        assert cfg.sampler == "parentbased_always_on"
        assert cfg.sampler_ratio is None
        assert cfg.exporter is None
        assert cfg.exporter_endpoint_env == "OTEL_EXPORTER_OTLP_ENDPOINT"
        assert cfg.exporter_headers_env == "OTEL_EXPORTER_OTLP_HEADERS"
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
                environment="prod",
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
        assert ctx["environment"] == "prod"
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
# Per-handler @traced_handler / @traced_action injection via the
# central handler renderer.  Goes through the real render registry so
# the resource/operation scope walking and import collection paths
# all run end to end.
# ---------------------------------------------------------------------------


_APP_ID = "project.apps.0"
_RESOURCE_ID = f"{_APP_ID}.resources.0"
_OP_ID = f"{_RESOURCE_ID}.operations.0"


def _handler_render_ctx(
    *,
    telemetry: TelemetryConfig | None,
    resource: ResourceConfig | None = None,
) -> RenderCtx:
    """Build a RenderCtx wired up to the real project/app/resource tree.

    Mirrors the ``_rctx`` helper in ``test_renderers.py`` but exposes
    the telemetry / resource overrides this test module needs.
    """
    resource = resource or ResourceConfig(model="myapp.models.Post")
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
    store = BuildStore(scope_tree=discover_scopes(ProjectConfig))
    store.register_instance("project", cfg)
    store.register_instance(_APP_ID, object(), parent="project")
    store.register_instance(_RESOURCE_ID, resource, parent=_APP_ID)
    store.register_instance(_OP_ID, object(), parent=_RESOURCE_ID)
    return RenderCtx(
        env=None,
        config=cfg,
        package_prefix="_generated",
        language="python",
        store=store,
        instance_id=_OP_ID,
    )


def _crud_handler() -> RouteHandler:
    return RouteHandler(
        method="GET",
        path="/{id}",
        function_name="get_post",
        op_name="get",
    )


def _action_handler() -> RouteHandler:
    return RouteHandler(
        method="POST",
        path="/{id}/publish",
        function_name="publish_post",
        op_name="publish",
        body_template="fastapi/ops/action.py.j2",
    )


def _decorators(handler: RouteHandler) -> list[str]:
    return list(handler.decorators)


def _all_imports(fragments) -> str:
    from foundry.imports import ImportCollector
    from foundry.render import SnippetFragment

    merged = ImportCollector()
    for f in fragments:
        if isinstance(f, SnippetFragment) and f.imports is not None:
            merged.update(f.imports)
    return merged.format("python")


class TestHandlerTracingDecorator:
    def test_no_decorator_when_telemetry_off(self):
        handler = _crud_handler()
        shared_registry.render(handler, _handler_render_ctx(telemetry=None))
        assert _decorators(handler) == []

    def test_traced_handler_added_for_crud(self):
        handler = _crud_handler()
        shared_registry.render(
            handler,
            _handler_render_ctx(telemetry=TelemetryConfig(service_name="svc")),
        )
        decs = _decorators(handler)
        assert len(decs) == 1
        assert decs[0].startswith("@traced_handler(")
        assert '"post.get"' in decs[0]
        assert 'resource="post"' in decs[0]
        assert 'op="get"' in decs[0]
        assert "record_exceptions=True" in decs[0]

    def test_traced_action_used_for_action_body_template(self):
        handler = _action_handler()
        shared_registry.render(
            handler,
            _handler_render_ctx(telemetry=TelemetryConfig(service_name="svc")),
        )
        decs = _decorators(handler)
        assert len(decs) == 1
        assert decs[0].startswith("@traced_action(")
        assert 'action="publish"' in decs[0]
        assert "op=" not in decs[0]

    def test_record_exceptions_threaded_through(self):
        handler = _crud_handler()
        shared_registry.render(
            handler,
            _handler_render_ctx(
                telemetry=TelemetryConfig(
                    service_name="svc",
                    record_exceptions=False,
                ),
            ),
        )
        assert "record_exceptions=False" in _decorators(handler)[0]

    def test_resource_trace_false_skips_decorator(self):
        handler = _crud_handler()
        resource = ResourceConfig(model="myapp.models.Post", trace=False)
        shared_registry.render(
            handler,
            _handler_render_ctx(
                telemetry=TelemetryConfig(service_name="svc"),
                resource=resource,
            ),
        )
        assert _decorators(handler) == []

    def test_span_per_handler_off_skips_decorator(self):
        handler = _crud_handler()
        shared_registry.render(
            handler,
            _handler_render_ctx(
                telemetry=TelemetryConfig(
                    service_name="svc",
                    span_per_handler=False,
                ),
            ),
        )
        assert _decorators(handler) == []

    def test_span_per_action_off_skips_action_decorator(self):
        handler = _action_handler()
        shared_registry.render(
            handler,
            _handler_render_ctx(
                telemetry=TelemetryConfig(
                    service_name="svc",
                    span_per_action=False,
                ),
            ),
        )
        assert _decorators(handler) == []

    def test_span_per_handler_off_does_not_disable_actions(self):
        # Action handlers are gated by span_per_action, not
        # span_per_handler -- the two flags are independent.
        handler = _action_handler()
        shared_registry.render(
            handler,
            _handler_render_ctx(
                telemetry=TelemetryConfig(
                    service_name="svc",
                    span_per_handler=False,
                    span_per_action=True,
                ),
            ),
        )
        assert _decorators(handler)[0].startswith("@traced_action(")

    def test_decorator_import_added(self):
        handler = _crud_handler()
        fragments = list(
            shared_registry.render(
                handler,
                _handler_render_ctx(
                    telemetry=TelemetryConfig(service_name="svc"),
                ),
            )
        )
        block = _all_imports(fragments)
        # Imports point at ingot.telemetry directly -- no per-project
        # re-export module sits between the generated handler and
        # the kiln-shipped decorator.
        assert "from ingot.telemetry import traced_handler" in block

    def test_action_decorator_import_added(self):
        handler = _action_handler()
        fragments = list(
            shared_registry.render(
                handler,
                _handler_render_ctx(
                    telemetry=TelemetryConfig(service_name="svc"),
                ),
            )
        )
        block = _all_imports(fragments)
        assert "from ingot.telemetry import traced_action" in block
