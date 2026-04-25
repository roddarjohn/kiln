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
    environment: 'prod',
    sampler: 'parentbased_traceidratio',
    sampler_ratio: 0.1,
    resource_attributes: { team: 'platform' },
  }),
}
```

`service_name` is required; everything else has a sensible default.
The full schema lives in
[`kiln.config.schema.TelemetryConfig`](api.html#kiln.config.schema.TelemetryConfig).

After regenerating, install the pinned OTel packages.  The cleanest
path is the kiln extra:

```sh
pip install kiln-generator[opentelemetry]
```

or, equivalently, from the requirements file the scaffold emits:

```sh
pip install -r _generated/telemetry/requirements.txt
```

Both pin the same coherent OTel release pair.  Either form is fine;
projects that vendor a `requirements.lock` typically use the file.

Then call `init_telemetry` from your app entry point, before mounting
the generated router:

```python
from fastapi import FastAPI
from _generated.routes import router
from _generated.telemetry.setup import init_telemetry

app = FastAPI()
init_telemetry(app)
app.include_router(router)
```

## What you get

| Signal | Source | Span / metric name |
|---|---|---|
| HTTP server span | `FastAPIInstrumentor` | one per request |
| Internal handler span | `@traced_handler` | `{resource}.{op}` |
| Internal action span | `@traced_action` | `{resource}.{action}` |
| DB client span | `SQLAlchemyInstrumentor` | per query |
| Metrics | OTLP `MeterProvider` | runtime + RED metrics |

Internal handler spans carry low-cardinality attributes for filtering:

- `kiln.resource` — e.g. `"article"`
- `kiln.op` — e.g. `"get"` (CRUD ops)
- `kiln.action` — e.g. `"publish"` (action ops only)

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
it builds the OTLP HTTP exporter with library defaults so the standard
OTel environment variables take effect at runtime:

```sh
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com
OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer abc123
```

This keeps the same artifact deployable across environments.  Override
with `exporter: 'otlp_grpc' | 'console' | 'none'` if you want to pin a
specific transport at code-generation time.  Note that `otlp_grpc`
requires you to also install
`opentelemetry-exporter-otlp-proto-grpc==1.29.0` on top of the
generated requirements file -- the gRPC stack is heavy and is not
included in the default pin set.

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

## Logs

OTel logs are off by default -- the SDK API is the youngest of the
three signals and is the most likely to churn.  Turn it on via:

```jsonnet
logs: true,
```

You almost certainly also want:

```jsonnet
instrument_logging: true,
```

so `LoggingInstrumentor` injects trace/span ids into stdlib log
records and your structured logger automatically correlates.

## Pinned versions

The generated `telemetry/requirements.txt` pins the OTel packages to a
coherent release pair:

```
opentelemetry-api==1.29.0
opentelemetry-sdk==1.29.0
opentelemetry-exporter-otlp-proto-http==1.29.0
opentelemetry-instrumentation-fastapi==0.50b0
opentelemetry-instrumentation-sqlalchemy==0.50b0
```

The instrumentation packages ride a separate `0.x.b` version line that
stabilises later than the core SDK; bump core (`1.x`) and
instrumentation (`0.x.b`) in lockstep.  The same pins live on the
`kiln-generator[opentelemetry]` extra and in the generated
`requirements.txt`, so there is one source of truth across kiln, the
generated app, and any explicit installs.
