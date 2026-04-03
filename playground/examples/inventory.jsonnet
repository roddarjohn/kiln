// Inventory example — exercises float/int/date fields, full auth, and actions.
//
// Consumer Python models are defined in inventory/models.py (not generated).
//
// Demonstrates:
//   - Full CRUD with specific fields and per-operation auth
//   - Create-only (append-only pattern) with date field and action
//   - write_only preset with route_prefix, db_key, and email/json/datetime fields
//   - Actions with and without params, with and without auth

local resource = import "kiln/resources/presets.libsonnet";

{
  version: "1",
  module: "inventory",

  resources: [
    // Products: full CRUD, mutations require auth, explicit route prefix
    {
      model: "inventory.models.Product",
      pk: "id",
      pk_type: "uuid",
      route_prefix: "/products",
      require_auth: false,

      operations: [
        "get",
        {
          name: "list",
          fields: [
            { name: "id", type: "uuid" },
            { name: "sku", type: "str" },
            { name: "name", type: "str" },
            { name: "unit_price", type: "float" },
            { name: "active", type: "bool" },
          ],
          // Filtering: search by sku/name/unit_price/active
          filters: {
            fields: ["sku", "name", "unit_price", "active"],
          },
          // Ordering: sort by name or unit_price
          ordering: {
            fields: ["name", "unit_price"],
            default: "name",
            default_dir: "asc",
          },
          // Keyset pagination on the primary key
          pagination: {
            mode: "keyset",
            cursor_field: "id",
            cursor_type: "uuid",
            default_page_size: 25,
            max_page_size: 100,
          },
        },
        {
          name: "create",
          require_auth: true,
          fields: [
            { name: "sku", type: "str" },
            { name: "name", type: "str" },
            { name: "unit_price", type: "float" },
            { name: "active", type: "bool" },
          ],
        },
        {
          name: "update",
          require_auth: true,
          fields: [
            { name: "name", type: "str" },
            { name: "unit_price", type: "float" },
            { name: "active", type: "bool" },
          ],
        },
        { name: "delete", require_auth: true },
        // Parameterised action, requires auth
        resource.action(
          name="stock_levels_by_date",
          fn="inventory.queries.stock_levels_by_date",
          require_auth=true,
        ),
        // No-param action, no auth — demonstrates both booleans
        resource.action(
          name="ping",
          fn="inventory.actions.ping_product",
          require_auth=false,
        ),
      ],
    },

    // StockMovements: create-only (append-only pattern)
    {
      model: "inventory.models.StockMovement",
      pk: "id",
      pk_type: "uuid",
      require_auth: false,

      operations: [
        {
          name: "create",
          require_auth: true,
          fields: [
            { name: "product_id", type: "uuid" },
            { name: "quantity", type: "int" },
            { name: "reason", type: "str" },
            { name: "movement_date", type: "date" },
          ],
        },
      ],
    },

    // EventLog: write_only (create/update/delete, no reads) backed by
    // PGCraftAppendOnly — the analytics DB keeps the full attribute history.
    // Demonstrates: write_only preset, route_prefix, db_key, email/json/datetime
    //               fields, require_auth: true, and a no-param no-auth action.
    resource.write_only(
      "inventory.models.EventLog",
      pk="id",
      pk_type="uuid",
      db_key="analytics",
      require_auth=true,
    ) + {
      route_prefix: "/event-logs",
      operations: [
        {
          name: "create",
          fields: [
            { name: "event_type", type: "str" },
            { name: "actor_email", type: "email" },
            { name: "payload", type: "json" },
            { name: "occurred_at", type: "datetime" },
          ],
        },
        {
          name: "update",
          fields: [
            { name: "event_type", type: "str" },
            { name: "payload", type: "json" },
          ],
        },
        "delete",
        resource.action(
          name="ping",
          fn="inventory.actions.ping_event_log",
          require_auth=false,
        ),
      ],
    },
  ],
}
