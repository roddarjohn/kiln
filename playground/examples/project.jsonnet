// Project-level configuration for be.
//
// Declares auth, databases, and all apps in one place.
// Run with:
//   just generate          (from playground/)
//   just rg                (reset + generate)

local auth = import "be/auth/jwt.libsonnet";
local db = import "be/db/databases.libsonnet";

{
  // -------------------------------------------------------------------------
  // Auth — shared across all apps.
  // -------------------------------------------------------------------------
  auth: auth.jwt({
    credentials_schema: "myapp.auth.LoginCredentials",
    session_schema: "myapp.auth.Session",
    validate_fn: "myapp.auth.validate_login",
    // Consumer-supplied ingot.auth.SessionStore.  Wires a
    // deny-list into get_session and revoke into logout.
    session_store: "myapp.revocation.revocations",
    secret_env: "JWT_SECRET",
    token_url: "/auth/token",
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
