// kiln stdlib — database connection helpers
//
// Usage:
//   local db = import 'kiln/db/databases.libsonnet';
//
//   databases: [
//     db.postgres("primary"),                          // default key
//     db.postgres("analytics", { url_env: "ANALYTICS_DB_URL" }),
//   ],
{
  // A single async PostgreSQL database connection.
  //
  // Fields:
  //   key      — identifier used in model/route config (e.g. "primary")
  //   url_env  — environment variable that holds the connection URL
  //   echo     — log all SQL to stderr (default false, useful for debugging)
  //   pool_size — max number of connections in the pool (default 5)
  //   default  — whether this is the db used when a route omits db_key
  //              (exactly one database should have default: true)
  postgres(key, opts={}):: {
    key: key,
    url_env: std.get(opts, "url_env", "DATABASE_URL"),
    echo: std.get(opts, "echo", false),
    pool_size: std.get(opts, "pool_size", 5),
    default: std.get(opts, "default", false),
  },
}
