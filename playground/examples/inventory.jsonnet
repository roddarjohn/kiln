// Inventory example — exercises float/int/date fields, full auth, and actions.
//
// Consumer Python models are defined in inventory/models.py (not generated).
//
// Run with:
//   uv run --group playground python playground/run_example.py examples/inventory.jsonnet

local auth = import "kiln/auth/jwt.libsonnet";
local resource = import "kiln/resources/presets.libsonnet";

{
  version: "1",
  module: "inventory",

  auth: auth.jwt({ secret_env: "INVENTORY_JWT_SECRET" }),

  resources: [
    // Products: full CRUD, mutations require auth
    {
      model: "inventory.models.Product",
      pk: "id",
      pk_type: "uuid",
      require_auth: ["create", "update", "delete"],

      get: true,
      list: {
        fields: [
          { name: "id", type: "uuid" },
          { name: "sku", type: "str" },
          { name: "name", type: "str" },
          { name: "unit_price", type: "float" },
          { name: "active", type: "bool" },
        ],
      },
      create: {
        fields: [
          { name: "sku", type: "str" },
          { name: "name", type: "str" },
          { name: "unit_price", type: "float" },
          { name: "active", type: "bool" },
        ],
      },
      update: {
        fields: [
          { name: "name", type: "str" },
          { name: "unit_price", type: "float" },
          { name: "active", type: "bool" },
        ],
      },
      delete: true,
      actions: [],
    },

    // StockMovements: create-only (append-only pattern), with a postgres action
    {
      model: "inventory.models.StockMovement",
      pk: "id",
      pk_type: "uuid",
      require_auth: ["create"],

      get: false,
      list: false,
      create: {
        fields: [
          { name: "product_id", type: "uuid" },
          { name: "quantity", type: "int" },
          { name: "reason", type: "str" },
          { name: "movement_date", type: "date" },
        ],
      },
      update: false,
      delete: false,

      actions: [
        resource.action(
          name="stock_levels_by_date",
          fn="inventory.queries.stock_levels_by_date",
          params=[
            { name: "start_date", type: "date" },
            { name: "end_date", type: "date" },
          ],
          require_auth=true,
        ),
      ],
    },
  ],
}
