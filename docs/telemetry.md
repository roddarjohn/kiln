# OpenTelemetry

Generated apps can emit OpenTelemetry traces, metrics, and (optionally)
logs.  Telemetry is fully **opt-in**: when the project config does not
set `telemetry`, the generated tree contains zero references to OTel
and the runtime cost is exactly zero.

## Enabling

Add a `telemetry` block to your project config:

```jsonnet
local kiln = import 'kiln/lib.libsonnet';
local telemetry = import 'kiln/telemetry/telemetry.libsonnet';

{
  databases: [...],
  apps: [...],
  telemetry: telemetry.otel('my-service-name', {
    sampler: 'parentbased_traceidratio',
    sampler_ratio: 0.1,
    resource_attributes: { team: 'platform' },
  }),
}
```

`service_name` is required; everything else has a sensible default.
The full schema lives in
[`kiln.config.schema.TelemetryConfig`](api.html#kiln.config.schema.TelemetryConfig).

After regenerating, install the pinned OTel package set via the
kiln extra:

```sh
pip install kiln-generator[opentelemetry]
```

Generated apps already depend on `kiln-generator` (they import from
`ingot`), so the extra is the single source of truth for OTel
versions -- nothing extra to vendor or copy.

Then call `init_telemetry` from your app entry point, before mounting
the generated router:

```python
from fastapi import FastAPI
from _generated.routes import router
from _generated.telemetry import init_telemetry

app = FastAPI()
init_telemetry(app)
app.include_router(router)
```

## Deployment environment

The `deployment.environment.name` resource attribute (dev / staging /
prod) is intentionally **not** a code-gen argument: the same artifact
should ship across environments.  It's read from the env var named by
`environment_env` at startup (default: `ENVIRONMENT`):

```sh
ENVIRONMENT=prod ./run-app
```

Override the variable name if your deployment already exports a
different one:

```jsonnet
telemetry.otel('my-service-name', {
  environment_env: 'DEPLOY_ENV',
})
```

When the variable is unset (or empty), the attribute is omitted.

## What you get

| Signal | Source | Span / metric name |
|---|---|---|
| HTTP server span | `FastAPIInstrumentor` | one per request |
| Internal handler span | `@traced_handler` | `{resource}.{op}` (CRUD or action) |
| DB client span | `SQLAlchemyInstrumentor` | per query |
| Outbound HTTP (`httpx`) | `HTTPXClientInstrumentor` | opt-in via `instrument_httpx` |
| Outbound HTTP (`requests`) | `RequestsInstrumentor` | opt-in via `instrument_requests` |
| Metrics | OTLP `MeterProvider` | wired; user code emits |

Internal handler spans carry low-cardinality attributes for filtering:

- `kiln.resource` — e.g. `"article"`
- `kiln.op` — e.g. `"get"` for CRUD, `"publish"` for actions

Both CRUD ops and user-defined actions go through the same
`@traced_handler` decorator and the same `kiln.op` attribute — the
*value* discriminates (kiln's CRUD names are a fixed small set;
anything else is a user-defined action).

## Sampler defaults

The default sampler is `parentbased_always_on`: friendly for
development, expensive in production.  Production deployments
typically switch to:

```jsonnet
sampler: 'parentbased_traceidratio',
sampler_ratio: 0.05,
```

Sampling at 5% with parent-based propagation gives you full traces for
sampled requests while keeping ingest volume manageable.

## Exporter

By default the generated `init_telemetry` does not pin a transport --
it instantiates the OTLP HTTP exporter with no arguments, and the OTel
SDK reads the standard environment variables itself at construct time:

```sh
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com
OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer abc123
```

This keeps the same artifact deployable across environments.  Override
with `exporter: 'otlp_grpc' | 'console' | 'none'` if you want to pin a
specific transport at code-generation time.

There are no kiln-side knobs for the env-var *names* — point your
deployment at the standard OTel ones.

`otlp_grpc` is additive: install it alongside the base extra,

```sh
pip install 'kiln-generator[opentelemetry,opentelemetry-grpc]'
```

The gRPC exporter lives in its own extra because it pulls in protobuf
and grpc-io -- roughly an order of magnitude heavier than the HTTP
transport.  Generated code imports the gRPC exporter lazily, so apps
that stay on OTLP/HTTP never load the gRPC stack even when both
extras are present.

## Per-resource and per-op opt-out

The project-level `span_per_handler` / `span_per_action` toggles control
tracing globally.  Hot-path or low-value resources can opt out without
disabling telemetry overall:

```jsonnet
{
  model: 'health.models.Probe',
  trace: false,  // skip spans for every op on this resource
  operations: [
    { name: 'get' },
  ],
}
```

The same field works per-operation:

```jsonnet
{ name: 'list', trace: false }  // skip the spans for this op only
```

The HTTP server span from `FastAPIInstrumentor` is unaffected by these
overrides -- they only suppress kiln's internal handler/action span
and its `kiln.resource` / `kiln.op` attributes.

## PII and the auth router

`capture_request_body` and `capture_response_body` default to **off**
because request and response payloads commonly contain PII.  Even when
they are turned on, the generated auth router (`auth/router.py`)
explicitly scrubs:

- `http.request.body`, `http.response.body`
- `http.request.header.authorization`
- `http.request.header.cookie`, `http.response.header.set-cookie`

with a `[scrubbed]` placeholder via
`scrub_current_span_attributes(...)`.  A placeholder rather than
attribute removal so a "missing `http.request.body`" alert doesn't
mask a real outage.

## Logging

kiln does not generate logging calls in CRUD handlers -- *you* emit
logs, and the two telemetry knobs below decide what happens to them.
Both are off by default.

### Library assumption

kiln assumes the **stdlib `logging`** module.  Loguru and structlog
both interoperate with stdlib (loguru via `InterceptHandler`,
structlog via `LoggerFactory(stdlib=True)`); set them up that way and
the rest of this section applies unchanged.

### `instrument_logging`: trace correlation

```jsonnet
instrument_logging: true,
```

Wires `opentelemetry.instrumentation.logging.LoggingInstrumentor`,
which patches `logging.LogRecord` so every record carries
`otelTraceID`, `otelSpanID`, `otelTraceSampled`, and
`otelServiceName`.  The default log format string is also updated to
include them.  This does **not** export logs anywhere -- it only adds
trace IDs to whatever sink you're already using (stdout, file, syslog,
etc.).  Use it when your logs go to a different backend than your
traces and you want to jump between them.

### `logs`: OTLP log export

```jsonnet
logs: true,
```

Builds an `opentelemetry.sdk._logs.LoggerProvider`, installs it
globally, and attaches an `opentelemetry.sdk._logs.LoggingHandler` to
the **stdlib root logger** at level `NOTSET` so every record routes
through OTLP alongside your traces and metrics.  The handler runs
*in addition* to your existing handlers -- `print`-style stdout logs
keep working; OTLP just becomes another sink.

Two consequences worth knowing:

1. The root logger needs a level set somewhere (`logging.basicConfig`,
   uvicorn's logging config, etc.) for any record to actually reach
   the handler.  `NOTSET` defers to the loggers' levels, it doesn't
   override them upward.
2. The OTel logs SDK API is the youngest of the three signals and is
   most likely to churn between OTel releases.  Pin versions tightly
   (the `[opentelemetry]` extra already does this) and re-test on
   upgrade.

### Combining

Most teams that turn on either knob want both:

```jsonnet
logs: true,
instrument_logging: true,
```

Records flow:

```
your code
  -> logging.getLogger().info(...)
       -> LoggingInstrumentor adds trace IDs to the record
       -> root logger handlers (stdout, etc.) fire
       -> OTLP LoggingHandler fires, ships to the collector
```

## Emitting your own signals

`init_telemetry` installs the global tracer, meter, and logger
providers — anything in your code can ask the OTel API for them and
start emitting.  No kiln-side wiring required.

### Custom traces

Get a tracer once at module level; start spans where you need them.
Spans nest automatically under whatever's active, so a span started
inside a handler ends up under that handler's `@traced_handler`
span:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

async def publish_article(article, db, body):
    with tracer.start_as_current_span("render_markdown") as span:
        span.set_attribute("article.length", len(body.content))
        rendered = render(body.content)
    # rest of the action…
```

Span attributes are **per-span**, so high-cardinality values like
`article.id` are fine here.  Don't put them on metric attributes
(see below).

### Custom metrics

Get a meter once, register an instrument once at module level, record
on it from anywhere.  Counters, histograms, up-down counters, and
observable gauges are all available:

```python
from opentelemetry import metrics

meter = metrics.get_meter(__name__)

published_total = meter.create_counter(
    "blog.articles.published",
    unit="1",
    description="Articles successfully published.",
)
publish_latency = meter.create_histogram(
    "blog.articles.publish_duration",
    unit="ms",
    description="Time spent in the publish action.",
)

async def publish_article(article, db, body):
    started = time.monotonic()
    # …work…
    published_total.add(1, {"author_type": article.author.type})
    publish_latency.record(
        (time.monotonic() - started) * 1000,
        {"author_type": article.author.type},
    )
```

**Cardinality matters here.**  Metric attributes are *dimensions* —
each unique combination is a separate time series at the backend.
Use small enumerations (`author_type ∈ {staff, guest}`), never
per-row identifiers (`article.id`).  Put the high-cardinality stuff
on a span attribute instead.

### Custom logs

Use stdlib `logging`.  With `instrument_logging=True`, every record
gets `otelTraceID` / `otelSpanID` injected; with `logs=True`, every
record also ships over OTLP via the handler kiln attaches to the
root logger.  You don't need to import anything OTel-specific:

```python
import logging

logger = logging.getLogger(__name__)

async def publish_article(article, db, body):
    logger.info(
        "publishing article",
        extra={"article_id": str(article.id), "kind": body.kind},
    )
```

If you use loguru or structlog, route them through stdlib (loguru's
`InterceptHandler`, structlog's `LoggerFactory(stdlib=True)`) and the
two toggles still work unchanged.

### Naming and gotchas

- **Tracer / meter / logger names** are conventionally `__name__`.
  They populate the *instrumentation scope* facet at the backend —
  keep them stable so dashboards stay readable.
- **Resource attributes vs span/metric attributes.** Resource
  attributes (set once at `init_telemetry`, e.g. `service.name`,
  `team`) describe the *service*; span and metric attributes
  describe an *event*.  Don't repeat resource attributes per span.
- **Imports.**  Use `from opentelemetry import metrics` (not
  `import opentelemetry.metrics`) — the SDK assumes the former
  import shape for some of its internal lazy-loading.

## Pinned versions

The `kiln-generator[opentelemetry]` extra pins the OTel packages to a
coherent release pair:

```
opentelemetry-api==1.29.0
opentelemetry-sdk==1.29.0
opentelemetry-exporter-otlp-proto-http==1.29.0
opentelemetry-instrumentation-fastapi==0.50b0
opentelemetry-instrumentation-sqlalchemy==0.50b0
opentelemetry-instrumentation-requests==0.50b0
```

The optional `kiln-generator[opentelemetry-grpc]` extra adds:

```
opentelemetry-exporter-otlp-proto-grpc==1.29.0
```

The instrumentation packages ride a separate `0.x.b` version line that
stabilises later than the core SDK; bump core (`1.x`) and
instrumentation (`0.x.b`) in lockstep when upgrading.
