// be stdlib — database connection helpers
//
// Usage:
//   local db = import 'be/db/databases.libsonnet';
//
//   databases: [
//     db.postgres("primary"),                          // default key
//     db.postgres("analytics", { url_env: "ANALYTICS_DB_URL" }),
//   ],
{
  // A single async PostgreSQL database connection.
  //
  // Fields:
  //   key          — identifier used in model/route config (e.g. "primary")
  //   url_env      — environment variable that holds the connection URL
  //   echo         — log all SQL to stderr (default false, useful for debugging)
  //   pool_size    — max number of connections kept open (default 5)
  //   max_overflow — extra connections allowed above pool_size (default 10)
  //   pool_timeout — seconds to wait for a connection before raising (default 30)
  //   pool_recycle — seconds before a connection is recycled; -1 disables (default -1)
  //   pool_pre_ping — test connections before use to detect stale ones (default true)
  //   default      — whether this is the db used when a route omits db_key
  //                  (exactly one database should have default: true)
  postgres(key, opts={}):: {
    key: key,
    url_env: std.get(opts, "url_env", "DATABASE_URL"),
    echo: std.get(opts, "echo", false),
    pool_size: std.get(opts, "pool_size", 5),
    max_overflow: std.get(opts, "max_overflow", 10),
    pool_timeout: std.get(opts, "pool_timeout", 30),
    pool_recycle: std.get(opts, "pool_recycle", -1),
    pool_pre_ping: std.get(opts, "pool_pre_ping", true),
    default: std.get(opts, "default", false),
  },
}
