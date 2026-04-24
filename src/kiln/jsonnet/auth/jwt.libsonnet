// kiln stdlib — auth config helper
// Usage: local auth = import 'kiln/auth/jwt.libsonnet';
//        auth.jwt({
//          credentials_schema: "myapp.auth.LoginCredentials",
//          session_schema: "myapp.auth.Session",
//          validate_fn: "myapp.auth.validate_login",
//          get_session_fn: "myapp.auth.get_session",
//        })
//
// All four dotted paths are required.  The consumer owns the auth
// module; kiln imports these symbols from it.  See ingot.auth for
// building-block helpers the consumer's module can use.
{
  jwt(opts):: {
    // Dotted path to the Pydantic model (or discriminated-union
    // type alias) accepted as the login request body.
    credentials_schema: opts.credentials_schema,
    // Dotted path to the Pydantic model carried in the token and
    // returned by get_session_fn.
    session_schema: opts.session_schema,
    // Dotted path to `(creds) -> Session | None`.  Returns the
    // session model on success or None to reject with 401.
    validate_fn: opts.validate_fn,
    // Dotted path to the FastAPI dependency that validates the
    // incoming token/cookie and returns the session model.
    get_session_fn: opts.get_session_fn,
    type: std.get(opts, "type", "jwt"),
    secret_env: std.get(opts, "secret_env", "JWT_SECRET"),
    algorithm: std.get(opts, "algorithm", "HS256"),
    token_url: std.get(opts, "token_url", "/auth/token"),
    // Cookie-transport options; only used when type == "cookie".
    cookie_name: std.get(opts, "cookie_name", "access_token"),
    cookie_secure: std.get(opts, "cookie_secure", true),
    cookie_samesite: std.get(opts, "cookie_samesite", "lax"),
  },
}
