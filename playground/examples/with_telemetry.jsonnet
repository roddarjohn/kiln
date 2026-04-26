local telemetry =
  import "../../src/kiln/jsonnet/telemetry/telemetry.libsonnet";
local base = import "project.jsonnet";

base {
  telemetry: telemetry.otel("blog-api", {
    sampler: "parentbased_traceidratio",
    sampler_ratio: 0.25,
  }),
}
