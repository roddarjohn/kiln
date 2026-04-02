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
  },
}
