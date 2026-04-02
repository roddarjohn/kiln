// Project-level configuration for kiln.
//
// Controls auth strategy and database connections.  Referenced by
// kiln init (scaffolding) and kiln generate (route/session wiring).
//
// Run init with:
//   kiln init --config examples/project.jsonnet --out src/app

local auth = import "kiln/auth/jwt.libsonnet";
local db = import "kiln/db/databases.libsonnet";

{
  // -------------------------------------------------------------------------
  // Auth
  // -------------------------------------------------------------------------
  // Supported types: "jwt", "cookie", "api_key", "none"
  // Remove the auth key entirely to generate no auth scaffolding.
  auth: auth.jwt({
    secret_env: "JWT_SECRET",
    token_url: "/auth/token",
    exclude_paths: ["/docs", "/openapi.json", "/health", "/metrics"],
  }),

  // -------------------------------------------------------------------------
  // Databases
  // -------------------------------------------------------------------------
  // Each entry needs a unique key.  Routes use db_key to pick a connection;
  // the database marked default: true is used when db_key is omitted.
  //
  // Examples:
  //   Single database (most apps):
  //     databases: [db.postgres("primary", { default: true })],
  //
  //   Primary + read replica:
  //     databases: [
  //       db.postgres("primary", { default: true }),
  //       db.postgres("replica", { url_env: "REPLICA_DATABASE_URL" }),
  //     ],
  //
  //   No database (API-proxy or pure-logic app):
  //     databases: [],
  databases: [
    db.postgres("primary", { default: true }),
    db.postgres("analytics", {
      url_env: "ANALYTICS_DATABASE_URL",
      pool_size: 2,
    }),
  ],
}
