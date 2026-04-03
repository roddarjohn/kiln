// Blog example — exercises plain (non-parameterised) views, a variety of
// field types, mixed CRUD configs, and no auth.
//
// Run with:
//   uv run --group playground python playground/run_example.py examples/blog.jsonnet

local crud = import "kiln/crud/presets.libsonnet";
local field = import "kiln/models/fields.libsonnet";
local factories = import "kiln/pgcraft/factories.libsonnet";

local timestamps = [
  field.datetime("created_at", auto_now_add=true),
  field.datetime("updated_at", auto_now=true),
];

{
  version: "1",
  module: "blog",

  models: [
    {
      name: "Author",
      table: "authors",
      schema: "public",
      pgcraft_type: factories.simple,
      fields: [
        field.uuid("id", primary_key=true),
        field.str("name"),
        field.email("email", unique=true),
        field.str("bio", nullable=true),
      ] + timestamps,
    },
    {
      name: "Article",
      table: "articles",
      schema: "public",
      pgcraft_type: factories.simple,
      fields: [
        field.uuid("id", primary_key=true),
        field.str("title"),
        field.str("slug", unique=true),
        field.str("body"),
        field.bool("published"),
        field.uuid("author_id", foreign_key="authors.id"),
        field.json("meta", nullable=true),
      ] + timestamps,
    },
    {
      name: "Tag",
      table: "tags",
      schema: "public",
      pgcraft_type: factories.simple,
      fields: [
        field.int("id", primary_key=true),
        field.str("name", unique=true),
      ],
    },
  ],

  views: [
    {
      name: "published_articles",
      schema: "public",
      returns: [
        { name: "id", type: "uuid" },
        { name: "title", type: "str" },
        { name: "slug", type: "str" },
        { name: "author_name", type: "str" },
        { name: "published_at", type: "datetime" },
      ],
    },
  ],

  routes: [
    { type: "crud", model: "Author", crud: crud.read_only({}) },
    { type: "crud", model: "Article", crud: crud.full({}) },
    { type: "crud", model: "Tag", crud: crud.full({}) },
    { type: "view", view: "published_articles", require_auth: false, http_method: "GET" },
  ],
}
