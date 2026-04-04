// kiln stdlib — JWT auth helper
// Usage: local auth = import 'kiln/auth/jwt.libsonnet';
//        auth.jwt({ secret_env: "MY_SECRET" })
{
  jwt(opts={}):: {
    type: "jwt",
    secret_env: std.get(opts, "secret_env", "JWT_SECRET"),
    algorithm: std.get(opts, "algorithm", "HS256"),
    token_url: std.get(opts, "token_url", "/auth/token"),
    exclude_paths: std.get(opts, "exclude_paths", [
      "/docs",
      "/openapi.json",
      "/health",
    ]),
    // Optional dotted path to a custom get_current_user dependency.
    // When set, auth/dependencies.py re-exports this function instead
    // of containing the default JWT implementation.
    // e.g. get_current_user_fn: "myapp.auth.custom.get_current_user"
    [if std.objectHas(opts, "get_current_user_fn") then "get_current_user_fn"]:
      std.get(opts, "get_current_user_fn"),
    // Dotted path to a credential-verification function.
    // Must accept (username, password) and return a dict (JWT
    // payload) on success or None on failure.
    // Required when get_current_user_fn is not set.
    // e.g. verify_credentials_fn: "myapp.auth.verify_credentials"
    [if std.objectHas(opts, "verify_credentials_fn") then "verify_credentials_fn"]:
      std.get(opts, "verify_credentials_fn"),
  },
}
