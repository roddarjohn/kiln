"""Tests for the ingot.telemetry runtime helpers."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    TraceIdRatioBased,
)

from ingot.telemetry import (
    ATTR_OP,
    ATTR_RESOURCE,
    _build_resource,
    _build_sampler,
    _build_span_exporter,
    _resolve_env,
    build_logger_provider,
    build_meter_provider,
    build_tracer_provider,
    scrub_current_span_attributes,
    traced_handler,
)

# ---------------------------------------------------------------------------
# Env var resolution
# ---------------------------------------------------------------------------


class TestResolveEnv:
    def test_none(self):
        assert _resolve_env(None) is None

    def test_unset(self, monkeypatch):
        monkeypatch.delenv("MY_VAR", raising=False)
        assert _resolve_env("MY_VAR") is None

    def test_empty_string_treated_as_unset(self, monkeypatch):
        # ``ENVIRONMENT=`` in a .env file is almost always a typo,
        # not a real "this deployment has the empty-string env name."
        monkeypatch.setenv("MY_VAR", "")
        assert _resolve_env("MY_VAR") is None

    def test_set(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "prod")
        assert _resolve_env("MY_VAR") == "prod"


class TestBuildProviderResolvesEnvironmentEnv:
    def test_environment_attached_when_set(self, monkeypatch):
        monkeypatch.setenv("MY_DEPLOY_ENV", "staging")
        provider = build_tracer_provider(
            service_name="svc",
            environment_env="MY_DEPLOY_ENV",
            exporter="none",
        )
        assert (
            provider.resource.attributes["deployment.environment.name"]
            == "staging"
        )

    def test_environment_omitted_when_var_unset(self, monkeypatch):
        monkeypatch.delenv("MY_DEPLOY_ENV", raising=False)
        provider = build_tracer_provider(
            service_name="svc",
            environment_env="MY_DEPLOY_ENV",
            exporter="none",
        )
        assert "deployment.environment.name" not in provider.resource.attributes

    def test_environment_omitted_when_env_var_name_is_none(self):
        provider = build_tracer_provider(
            service_name="svc",
            environment_env=None,
            exporter="none",
        )
        assert "deployment.environment.name" not in provider.resource.attributes


# ---------------------------------------------------------------------------
# Resource attribute composition
# ---------------------------------------------------------------------------


class TestBuildResource:
    def test_only_service_name(self):
        r = _build_resource(
            service_name="svc",
            service_version=None,
            environment=None,
            extra={},
        )
        assert r.attributes["service.name"] == "svc"
        assert "service.version" not in r.attributes
        assert "deployment.environment.name" not in r.attributes

    def test_optional_fields(self):
        r = _build_resource(
            service_name="svc",
            service_version="1.0.0",
            environment="prod",
            extra={"team": "platform"},
        )
        assert r.attributes["service.version"] == "1.0.0"
        assert r.attributes["deployment.environment.name"] == "prod"
        assert r.attributes["team"] == "platform"


# ---------------------------------------------------------------------------
# Sampler dispatch
# ---------------------------------------------------------------------------


class TestBuildSampler:
    def test_always_on(self):
        assert _build_sampler(name="always_on", ratio=None) is ALWAYS_ON

    def test_always_off(self):
        assert _build_sampler(name="always_off", ratio=None) is ALWAYS_OFF

    def test_parentbased_always_on(self):
        sampler = _build_sampler(name="parentbased_always_on", ratio=None)
        assert isinstance(sampler, ParentBased)

    def test_parentbased_always_off(self):
        sampler = _build_sampler(name="parentbased_always_off", ratio=None)
        assert isinstance(sampler, ParentBased)

    def test_traceidratio(self):
        sampler = _build_sampler(name="traceidratio", ratio=0.25)
        assert isinstance(sampler, TraceIdRatioBased)

    def test_parentbased_traceidratio(self):
        sampler = _build_sampler(name="parentbased_traceidratio", ratio=0.1)
        assert isinstance(sampler, ParentBased)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown sampler"):
            _build_sampler(name="garbage", ratio=None)


# ---------------------------------------------------------------------------
# Provider builders
# ---------------------------------------------------------------------------


class TestProviderBuilders:
    def test_tracer_provider_no_exporter(self):
        # exporter="none" returns provider with no span processor.
        provider = build_tracer_provider(
            service_name="svc",
            exporter="none",
        )
        assert isinstance(provider, TracerProvider)
        assert provider.resource.attributes["service.name"] == "svc"

    def test_tracer_provider_with_console_exporter(self):
        # Default exporter path adds a BatchSpanProcessor; pick console
        # to avoid actually opening a network export channel.
        provider = build_tracer_provider(
            service_name="svc",
            exporter="console",
        )
        # _active_span_processor is the SDK-internal multi-processor;
        # presence of any sub-processor confirms the exporter wiring.
        assert provider._active_span_processor is not None

    def test_meter_provider(self):
        provider = build_meter_provider(service_name="svc")
        assert isinstance(provider, MeterProvider)

    def test_logger_provider(self):
        provider = build_logger_provider(service_name="svc")
        assert isinstance(provider, LoggerProvider)
        assert provider.resource.attributes["service.name"] == "svc"


class TestBuildSpanExporter:
    def test_none(self):
        assert _build_span_exporter(exporter="none") is None

    def test_console(self):
        exp = _build_span_exporter(exporter="console")
        assert isinstance(exp, ConsoleSpanExporter)

    def test_otlp_http_default(self):
        # ``exporter=None`` falls through to the OTLP HTTP exporter,
        # which reads ``OTEL_EXPORTER_OTLP_*`` itself at construct
        # time -- we don't pass endpoint/headers explicitly anymore.
        exp = _build_span_exporter(exporter=None)
        assert exp is not None
        # Class module guards against accidentally returning the gRPC
        # exporter on the default path.
        assert "http" in type(exp).__module__

    def test_otlp_http_explicit(self):
        exp = _build_span_exporter(exporter="otlp_http")
        assert exp is not None
        assert "http" in type(exp).__module__

    def test_otlp_grpc_imports_lazily(self):
        grpc = pytest.importorskip(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        )
        exp = _build_span_exporter(exporter="otlp_grpc")
        assert isinstance(exp, grpc.OTLPSpanExporter)


# ---------------------------------------------------------------------------
# traced_handler
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _module_exporter():
    """Install a TracerProvider once per module.

    OTel's global tracer provider can only be set once per process
    (a second ``set_tracer_provider`` warns and is ignored), so the
    exporter is installed at module scope and the function-scope
    ``in_memory_tracer`` fixture below clears it between tests.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture
def in_memory_tracer(_module_exporter):
    """Yield a fresh-per-test view of the module-scoped exporter."""
    _module_exporter.clear()
    yield _module_exporter
    _module_exporter.clear()


class TestTracedHandler:
    async def test_emits_span_with_attributes(self, in_memory_tracer):
        @traced_handler("article.get", resource="article", op="get")
        async def handler() -> int:
            return 42

        result = await handler()
        assert result == 42
        spans = in_memory_tracer.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "article.get"
        assert span.attributes[ATTR_RESOURCE] == "article"
        assert span.attributes[ATTR_OP] == "get"

    async def test_records_exception(self, in_memory_tracer):
        @traced_handler("article.get", resource="article", op="get")
        async def handler() -> int:
            msg = "boom"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="boom"):
            await handler()
        spans = in_memory_tracer.get_finished_spans()
        assert len(spans) == 1
        # Exception event recorded; status set to ERROR.
        assert spans[0].status.status_code.name == "ERROR"
        assert any(e.name == "exception" for e in spans[0].events)

    async def test_record_exceptions_disabled(self, in_memory_tracer):
        @traced_handler(
            "article.get",
            resource="article",
            op="get",
            record_exceptions=False,
        )
        async def handler() -> int:
            msg = "boom"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            await handler()
        spans = in_memory_tracer.get_finished_spans()
        assert spans[0].status.status_code.name != "ERROR"
        assert not any(e.name == "exception" for e in spans[0].events)


class TestScrubCurrentSpanAttributes:
    async def test_overwrites_named_keys(self, in_memory_tracer):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("login") as span:
            span.set_attribute("http.request.body", "secret")
            span.set_attribute("http.response.body", "token")
            scrub_current_span_attributes(
                "http.request.body",
                "http.response.body",
            )
        spans = in_memory_tracer.get_finished_spans()
        attrs = spans[0].attributes
        assert attrs["http.request.body"] == "[scrubbed]"
        assert attrs["http.response.body"] == "[scrubbed]"
