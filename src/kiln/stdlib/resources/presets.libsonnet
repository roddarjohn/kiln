// kiln/resources/presets.libsonnet
//
// Helper functions for common resource configurations.
//
// Usage:
//   local resource = import "kiln/resources/presets.libsonnet";
//
//   resources: [
//     resource.full("myapp.models.Article", pk="id", pk_type="uuid") + {
//       operations: super.operations + [
//         resource.action("publish", "myapp.actions.publish"),
//       ],
//     },
//     resource.read_only("myapp.models.Tag"),
//   ],

{
  // All CRUD operations enabled; all ops use runtime inspection (all columns).
  // Override individual operations or extend with actions.
  full(model, pk="id", pk_type="uuid", db_key=null, require_auth=true):: {
    model: model,
    pk: pk,
    pk_type: pk_type,
    [if db_key != null then "db_key"]: db_key,
    require_auth: require_auth,
    operations: ["get", "list", "create", "update", "delete"],
  },

  // Read-only: get and list only, no mutations.
  read_only(model, pk="id", pk_type="uuid", db_key=null, require_auth=false):: {
    model: model,
    pk: pk,
    pk_type: pk_type,
    [if db_key != null then "db_key"]: db_key,
    require_auth: require_auth,
    operations: ["get", "list"],
  },

  // Write-only: create, update, delete — no read/list endpoints.
  write_only(model, pk="id", pk_type="uuid", db_key=null, require_auth=true):: {
    model: model,
    pk: pk,
    pk_type: pk_type,
    [if db_key != null then "db_key"]: db_key,
    require_auth: require_auth,
    operations: ["create", "update", "delete"],
  },

  // Shorthand for an action entry in the operations list.
  // fn is a dotted Python import path, e.g. "myapp.actions.publish".
  // The callable receives (pk, db=db, **params).
  action(name, fn, params=[], require_auth=true):: {
    name: name,
    fn: fn,
    [if std.length(params) > 0 then "params"]: params,
    [if require_auth != true then "require_auth"]: require_auth,
  },
}
