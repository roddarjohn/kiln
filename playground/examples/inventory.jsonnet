// Inventory example — exercises float/int/date fields, full auth, and actions.
//
// Consumer Python models are defined in inventory/models.py (not generated).
//
// Demonstrates:
//   - Full CRUD with specific fields and per-operation auth
//   - Create-only (append-only pattern) with date field and action
//   - Write-only resource with route_prefix, db_key, and
//     email/json/datetime fields
//   - Actions with and without params, with and without auth
//   - Structured filter blocks (enum / bool / ref / free_text)
//   - Resource search via `searchable: true` + `link:`
//   - Saved-view CRUD wired through the `serializer:` hook +
//     `resource.saved_views(...)` preset

local list = import "be/operations/list.libsonnet";
local resource = import "be/resources/presets.libsonnet";

{
  version: "1",
  module: "inventory",

  resources: [
    // Products: full CRUD, mutations require auth, explicit route prefix
    {
      model: "inventory.models.Product",
      pk: { name: "id", type: "uuid" },
      route_prefix: "/products",
      require_auth: false,

      operations: [
        {
          name: "get",
          fields: [
            { name: "id", type: "uuid" },
            { name: "sku", type: "str" },
            { name: "name", type: "str" },
            { name: "unit_price", type: "float" },
            { name: "stock_count", type: "int" },
            { name: "active", type: "bool" },
            { name: "available_from", type: "date" },
          ],
        },
        list.searchable(
          fields=[
            { name: "id", type: "uuid" },
            { name: "sku", type: "str" },
            { name: "name", type: "str" },
            { name: "unit_price", type: "float" },
            { name: "active", type: "bool" },
          ],
          // Structured filter spec — every entry declares its
          // operators, value source, and any source-specific
          // metadata so the discovery payload can drive a typed
          // filter UI.  Demonstrates every value-kind:
          //   self      — filter by Product's own id (eq / in)
          //   ref       — filter by FK to Customer (autocomplete
          //               into /customers/_values)
          //   free_text — ILIKE on string columns
          //   literal   — typed numeric input
          //   bool      — toggle
          filter={
            fields: [
              { name: "id", values: "self" },
              {
                name: "customer_id",
                values: "ref",
                ref_resource: "customer",
              },
              { name: "sku", values: "free_text" },
              { name: "name", values: "free_text" },
              {
                name: "unit_price",
                values: "literal",
                type: "float",
                operators: ["eq", "gte", "lte"],
              },
              { name: "active", values: "bool" },
            ],
          },
          order={
            fields: ["name", "unit_price"],
            default: "name",
            default_dir: "asc",
          },
          paginate={
            mode: "keyset",
            cursor_field: "id",
            cursor_type: "uuid",
            default_page_size: 25,
            max_page_size: 100,
          },
        ),
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
      pk: { name: "id", type: "uuid" },
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

    // EventLog: write-only (create/update/delete, no reads) backed by
    // PGCraftAppendOnly — the analytics DB keeps the full attribute
    // history.  Demonstrates: route_prefix, db_key, email/json/datetime
    // fields, require_auth: true, and a no-param no-auth action.
    {
      model: "inventory.models.EventLog",
      pk: { name: "id", type: "uuid" },
      route_prefix: "/event-logs",
      db_key: "analytics",
      require_auth: true,

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
        { name: "delete" },
        resource.action(
          name="ping",
          fn="inventory.actions.ping_event_log",
          require_auth=false,
        ),
      ],
    },
  ],
}
