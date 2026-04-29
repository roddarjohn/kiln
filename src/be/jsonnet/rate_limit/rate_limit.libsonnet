// be stdlib — slowapi rate-limit config helper
// Usage:
//   local rate_limit = import 'be/rate_limit/rate_limit.libsonnet';
//   {
//     rate_limit: rate_limit.slowapi('myapp.models.RateLimitBucket', {
//       default_limit: '100/minute',
//       headers_enabled: true,
//     }),
//   }
//
// Defaults mirror be.config.schema.RateLimitConfig: project-wide
// 60/minute default limit (one hit per second on average),
// client-IP key function, default database, X-RateLimit headers
// on.  Pass ``default_limit: null`` to disable the project-wide
// default and require per-op opt-in.
//
// The bucket model must mix in
// ``ingot.rate_limit.RateLimitBucketMixin`` and live at the dotted
// path passed as ``bucket_model``.  The consumer is responsible
// for migrating the table -- be doesn't generate Alembic migrations.
{
  slowapi(bucket_model, opts={}):: {
    bucket_model: bucket_model,
    [if std.objectHas(opts, "default_limit") then "default_limit"]:
      opts.default_limit,
    [if std.objectHas(opts, "key_func") then "key_func"]: opts.key_func,
    [if std.objectHas(opts, "db_key") then "db_key"]: opts.db_key,
    headers_enabled: std.get(opts, "headers_enabled", true),
  },
}
