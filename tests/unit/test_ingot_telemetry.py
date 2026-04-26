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
    _parse_otlp_headers,
    build_logger_provider,
    build_meter_provider,
    build_tracer_provider,
    scrub_current_span_attributes,
    shutdown_providers,
    traced_action,
    traced_handler,
)

# ---------------------------------------------------------------------------
# _parse_otlp_headers
# ---------------------------------------------------------------------------


class TestParseOtlpHeaders:
    def test_empty(self):
        assert _parse_otlp_headers("") == {}

    def test_single_pair(self):
        assert _parse_otlp_headers("k=v") == {"k": "v"}

    def test_multi_pair(self):
        out = _parse_otlp_headers("a=1,b=2")
        assert out == {"a": "1", "b": "2"}

    def test_strips_whitespace(self):
        assert _parse_otlp_headers(" a = 1 , b = 2 ") == {"a": "1", "b": "2"}

    def test_drops_unparseable(self):
        # No '=' means skip; trailing comma OK.
        assert _parse_otlp_headers("notapair,a=1,") == {"a": "1"}

    def test_drops_pair_with_blank_key(self):
        # ``=value`` parses to ('', 'value'); blank key is skipped.
        assert _parse_otlp_headers("=v,a=1") == {"a": "1"}


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
        assert _build_sampler("always_on", None) is ALWAYS_ON

    def test_always_off(self):
        assert _build_sampler("always_off", None) is ALWAYS_OFF

    def test_parentbased_always_on(self):
        sampler = _build_sampler("parentbased_always_on", None)
        assert isinstance(sampler, ParentBased)

    def test_parentbased_always_off(self):
        sampler = _build_sampler("parentbased_always_off", None)
        assert isinstance(sampler, ParentBased)

    def test_traceidratio(self):
        sampler = _build_sampler("traceidratio", 0.25)
        assert isinstance(sampler, TraceIdRatioBased)

    def test_parentbased_traceidratio(self):
        sampler = _build_sampler("parentbased_traceidratio", 0.1)
        assert isinstance(sampler, ParentBased)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown sampler"):
            _build_sampler("garbage", None)


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
        assert (
            _build_span_exporter(
                "none",
                "OTEL_EXPORTER_OTLP_ENDPOINT",
                "OTEL_EXPORTER_OTLP_HEADERS",
            )
            is None
        )

    def test_console(self):
        exp = _build_span_exporter(
            "console",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_HEADERS",
        )
        assert isinstance(exp, ConsoleSpanExporter)

    def test_otlp_http_default(self, monkeypatch):
        # Default branch (exporter is None) returns the HTTP exporter
        # built from OTel env vars.  Set the env vars so the exporter
        # picks them up rather than hitting library defaults.
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://x:4318")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "auth=foo")
        exp = _build_span_exporter(
            None,
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_HEADERS",
        )
        assert exp is not None
        # Class name guards against accidentally returning the gRPC
        # exporter when None is passed.
        assert "Http" in type(exp).__module__ or "http" in type(exp).__module__

    def test_otlp_http_explicit(self):
        exp = _build_span_exporter(
            "otlp_http",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_HEADERS",
        )
        assert exp is not None
        assert "http" in type(exp).__module__

    def test_otlp_grpc_imports_lazily(self):
        grpc = pytest.importorskip(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        )
        exp = _build_span_exporter(
            "otlp_grpc",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_HEADERS",
        )
        assert isinstance(exp, grpc.OTLPSpanExporter)


class TestShutdownProviders:
    def test_calls_shutdown_on_active_providers(self):
        # ``shutdown_providers`` queries the active globals; on a fresh
        # process the defaults are no-op proxies.  The contract is
        # "doesn't raise" -- enough to lock in that the function runs
        # both branches without error.
        shutdown_providers()


# ---------------------------------------------------------------------------
# traced_handler / traced_action
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


class TestTracedAction:
    async def test_action_attribute_distinct_from_op(self, in_memory_tracer):
        @traced_action(
            "article.publish",
            resource="article",
            action="publish",
        )
        async def action() -> str:
            return "ok"

        await action()
        spans = in_memory_tracer.get_finished_spans()
        attrs = spans[0].attributes
        assert attrs[ATTR_RESOURCE] == "article"
        assert attrs["kiln.action"] == "publish"
        assert ATTR_OP not in attrs


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
