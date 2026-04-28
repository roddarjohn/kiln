// be stdlib — auth config helper
// Usage: local auth = import 'be/auth/jwt.libsonnet';
//        auth.jwt({
//          credentials_schema: "myapp.auth.LoginCredentials",
//          session_schema: "myapp.auth.Session",
//          validate_fn: "myapp.auth.validate_login",
//          sources: ["bearer", "cookie"],  // optional, defaults to ["bearer"]
//        })
//
// The consumer owns the three types; be owns the auth package
// (get_session dep + login/logout routes).  See ingot.auth for the
// runtime primitives the generated code composes.
{
  jwt(opts):: {
    // Dotted path to the Pydantic model (or discriminated-union
    // type alias) accepted as the login request body.
    credentials_schema: opts.credentials_schema,
    // Dotted path to the Pydantic model carried in the token.
    session_schema: opts.session_schema,
    // Dotted path to `(creds) -> Session | None`.
    validate_fn: opts.validate_fn,
    // Ordered list of token transports: subset of {"bearer","cookie"}.
    sources: std.get(opts, "sources", ["bearer"]),
    secret_env: std.get(opts, "secret_env", "JWT_SECRET"),
    algorithm: std.get(opts, "algorithm", "HS256"),
    token_url: std.get(opts, "token_url", "/auth/token"),
    cookie_name: std.get(opts, "cookie_name", "access_token"),
    cookie_secure: std.get(opts, "cookie_secure", true),
    cookie_samesite: std.get(opts, "cookie_samesite", "lax"),
    // Optional dotted path to an ingot.auth.SessionStore instance.
    // When set, the generated get_session dep enforces the store's
    // deny-list and the generated logout calls store.revoke first.
    [if std.objectHas(opts, "session_store") then "session_store"]:
      opts.session_store,
  },
}
