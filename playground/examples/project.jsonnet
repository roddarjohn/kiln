// Project-level configuration for kiln.
//
// Declares auth, databases, and all apps in one place.
// Run with:
//   just generate          (from playground/)
//   just rg                (reset + generate)

local auth = import "kiln/auth/jwt.libsonnet";
local db = import "kiln/db/databases.libsonnet";

{
  // -------------------------------------------------------------------------
  // Auth — shared across all apps.
  // -------------------------------------------------------------------------
  auth: auth.jwt({
    secret_env: "JWT_SECRET",
    token_url: "/auth/token",
    exclude_paths: ["/docs", "/openapi.json", "/health", "/metrics"],
    verify_credentials_fn: "myapp.auth.verify_credentials",
  }),

  // -------------------------------------------------------------------------
  // Databases
  // -------------------------------------------------------------------------
  databases: [
    db.postgres("primary", { default: true }),
    db.postgres("analytics", {
      url_env: "ANALYTICS_DATABASE_URL",
      pool_size: 2,
    }),
  ],

  // -------------------------------------------------------------------------
  // Apps — each entry imports an app-level config and assigns a URL prefix.
  // -------------------------------------------------------------------------
  apps: [
    { config: import "blog.jsonnet", prefix: "/blog" },
    { config: import "inventory.jsonnet", prefix: "/inventory" },
  ],
}
