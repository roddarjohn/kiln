// Inventory example — exercises append-only pgcraft type, float/int/date
// fields, a POST parameterised function view, and full auth on all ops.
//
// Run with:
//   uv run --group playground python playground/run_example.py examples/inventory.jsonnet

local auth = import "kiln/auth/jwt.libsonnet";
local crud = import "kiln/crud/presets.libsonnet";
local field = import "kiln/models/fields.libsonnet";

{
  version: "1",
  module: "inventory",

  auth: auth.jwt({ secret_env: "INVENTORY_JWT_SECRET" }),

  models: [
    {
      name: "Product",
      table: "products",
      schema: "inventory",
      pgcraft_type: "simple",
      fields: [
        field.uuid("id", primary_key=true),
        field.str("sku", unique=true),
        field.str("name"),
        field.float("unit_price"),
        field.bool("active"),
        field.json("attributes", nullable=true),
        field.datetime("created_at", auto_now_add=true),
      ],
      crud: crud.full({ require_auth: ["create", "update", "delete"] }),
    },

    {
      // Append-only ledger of stock movements.
      name: "StockMovement",
      table: "stock_movements",
      schema: "inventory",
      pgcraft_type: "append_only",
      fields: [
        field.uuid("id", primary_key=true),
        field.uuid("product_id", foreign_key="inventory.products.id"),
        field.int("quantity"),
        field.str("reason", nullable=true),
        field.date("movement_date"),
        field.datetime("recorded_at", auto_now_add=true),
      ],
      // Write-only: create movements, no update/delete (append-only).
      crud: crud.no_list({ require_auth: ["create"] }),
    },
  ],

  // Parameterised function view with POST — returns stock levels per product
  // within a date range.
  views: [
    {
      name: "stock_levels_by_date",
      model: "StockMovement",
      description: "Aggregate stock levels per product for a date range.",
      schema: "inventory",
      http_method: "GET",
      require_auth: true,
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
}
