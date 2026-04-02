// Kiln playground example
// Demonstrates auth, models with CRUD, and a parameterised view (PG function).
//
// Run with:
//   uv run --group playground python playground/run_example.py

local auth = import "kiln/auth/jwt.libsonnet";
local crud = import "kiln/crud/presets.libsonnet";
local field = import "kiln/models/fields.libsonnet";

local timestamps = [
  field.datetime("created_at", auto_now_add=true),
  field.datetime("updated_at", auto_now=true),
];

{
  version: "1",
  module: "app",

  auth: auth.jwt({
    secret_env: "JWT_SECRET",
    token_url: "/auth/token",
    exclude_paths: ["/health", "/docs", "/openapi.json"],
  }),

  models: [
    {
      name: "User",
      table: "users",
      schema: "public",
      pgcraft_type: "simple",
      pgcraft_plugins: ["postgrest"],
      fields: [
        field.uuid("id", primary_key=true),
        field.email("email", unique=true),
        field.str("hashed_password", exclude_from_api=true),
      ] + timestamps,
      crud: crud.full({ require_auth: ["update", "delete", "list"] }),
    },

    {
      name: "Post",
      table: "posts",
      schema: "public",
      pgcraft_type: "simple",
      pgcraft_plugins: ["postgrest"],
      fields: [
        field.uuid("id", primary_key=true),
        field.str("title"),
        field.str("body", nullable=true),
        field.uuid("author_id", foreign_key="users.id"),
      ] + timestamps,
      crud: crud.full({ require_auth: ["create", "update", "delete"] }),
    },
  ],

  // Parameterised view → generates a PGCraftFunctionMixin stub
  // and a FastAPI route that calls the PG function via
  // func.public.summarize_posts_by_user(...).table_valued(...)
  views: [
    {
      name: "summarize_posts_by_user",
      model: "Post",
      description: "Count posts per user within a date range.",
      schema: "public",
      http_method: "GET",
      require_auth: true,
      parameters: [
        { name: "start_date", type: "date" },
        { name: "end_date", type: "date" },
      ],
      returns: [
        { name: "user_id", type: "uuid" },
        { name: "post_count", type: "int" },
      ],
    },
  ],
}
