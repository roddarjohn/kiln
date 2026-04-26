// kiln stdlib — OpenTelemetry config helper
// Usage:
//   local telemetry = import 'kiln/telemetry/telemetry.libsonnet';
//   {
//     telemetry: telemetry.otel('blog-api', {
//       sampler: 'parentbased_traceidratio',
//       sampler_ratio: 0.1,
//       resource_attributes: { team: 'platform' },
//     }),
//   }
//
// Defaults mirror kiln.config.schema.TelemetryConfig: traces and
// metrics on, logs off, FastAPI + SQLAlchemy auto-instrumented,
// per-handler spans on, request/response body capture off (PII
// risk), and `parentbased_always_on` sampling for friendly dev
// defaults.  Production users typically switch to a ratio sampler.
//
// Deployment environment (dev/staging/prod) is *not* a code-gen
// argument: the same artifact ships across environments, so the
// value is read from the env var named by `environment_env` at
// startup (default: `ENVIRONMENT`).  Override the variable name
// here if your deployment already exports a different one.
{
  otel(service_name, opts={}):: {
    service_name: service_name,
    [if std.objectHas(opts, "service_version") then "service_version"]:
      opts.service_version,
    environment_env: std.get(opts, "environment_env", "ENVIRONMENT"),

    traces: std.get(opts, "traces", true),
    metrics: std.get(opts, "metrics", true),
    logs: std.get(opts, "logs", false),

    instrument_fastapi: std.get(opts, "instrument_fastapi", true),
    instrument_sqlalchemy: std.get(opts, "instrument_sqlalchemy", true),
    instrument_httpx: std.get(opts, "instrument_httpx", false),
    instrument_requests: std.get(opts, "instrument_requests", false),
    instrument_logging: std.get(opts, "instrument_logging", false),

    span_per_handler: std.get(opts, "span_per_handler", true),
    span_per_action: std.get(opts, "span_per_action", true),
    record_exceptions: std.get(opts, "record_exceptions", true),

    capture_request_body: std.get(opts, "capture_request_body", false),
    capture_response_body: std.get(opts, "capture_response_body", false),

    sampler: std.get(opts, "sampler", "parentbased_always_on"),
    [if std.objectHas(opts, "sampler_ratio") then "sampler_ratio"]:
      opts.sampler_ratio,

    [if std.objectHas(opts, "exporter") then "exporter"]: opts.exporter,
    exporter_endpoint_env:
      std.get(opts, "exporter_endpoint_env", "OTEL_EXPORTER_OTLP_ENDPOINT"),
    exporter_headers_env:
      std.get(opts, "exporter_headers_env", "OTEL_EXPORTER_OTLP_HEADERS"),

    resource_attributes: std.get(opts, "resource_attributes", {}),
  },
}
