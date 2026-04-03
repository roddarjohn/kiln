// Inventory example — exercises append-only pgcraft type, float/int/date
// fields, a POST parameterised function view, and full auth on all ops.
//
// Run with:
//   uv run --group playground python playground/run_example.py examples/inventory.jsonnet

local auth = import "kiln/auth/jwt.libsonnet";
local crud = import "kiln/crud/presets.libsonnet";
local field = import "kiln/models/fields.libsonnet";
local factories = import "kiln/pgcraft/factories.libsonnet";

{
  version: "1",
  module: "inventory",

  auth: auth.jwt({ secret_env: "INVENTORY_JWT_SECRET" }),

  models: [
    {
      name: "Product",
      table: "products",
      schema: "inventory",
      pgcraft_type: factories.simple,
      fields: [
        field.uuid("id", primary_key=true),
        field.str("sku", unique=true),
        field.str("name"),
        field.float("unit_price"),
        field.bool("active"),
        field.json("attributes", nullable=true),
        field.datetime("created_at", auto_now_add=true),
      ],
    },
    {
      name: "StockMovement",
      table: "stock_movements",
      schema: "inventory",
      pgcraft_type: factories.append_only,
      fields: [
        field.uuid("id", primary_key=true),
        field.uuid("product_id", foreign_key="inventory.products.id"),
        field.int("quantity"),
        field.str("reason", nullable=true),
        field.date("movement_date"),
        field.datetime("recorded_at", auto_now_add=true),
      ],
    },
  ],

  views: [
    {
      name: "stock_levels_by_date",
      schema: "inventory",
      parameters: [
        { name: "start_date", type: "date" },
        { name: "end_date", type: "date" },
      ],
      returns: [
        { name: "product_id", type: "uuid" },
        { name: "sku", type: "str" },
        { name: "net_quantity", type: "int" },
      ],
    },
  ],

  routes: [
    { type: "crud", model: "Product", crud: crud.full({ require_auth: ["create", "update", "delete"] }) },
    { type: "crud", model: "StockMovement", crud: crud.no_list({ require_auth: ["create"] }) },
    { type: "view", view: "stock_levels_by_date", require_auth: true, http_method: "GET" },
  ],
}
