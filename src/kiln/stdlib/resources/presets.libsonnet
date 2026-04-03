// kiln/resources/presets.libsonnet
//
// Helper functions for common resource configurations.
//
// Usage:
//   local resource = import "kiln/resources/presets.libsonnet";
//
//   resources: [
//     resource.full("myapp.models.Article", pk="id", pk_type="uuid") + {
//       create: { fields: [{ name: "title", type: "str" }] },
//     },
//     resource.read_only("myapp.models.Tag"),
//   ],

{
  // All CRUD operations enabled; all ops use runtime inspection (all columns).
  // Override individual operations to restrict fields.
  full(model, pk="id", pk_type="uuid", db_key=null, require_auth=true):: {
    model: model,
    pk: pk,
    pk_type: pk_type,
    [if db_key != null then "db_key"]: db_key,
    require_auth: require_auth,
    get: true,
    list: true,
    create: true,
    update: true,
    delete: true,
    actions: [],
  },

  // Read-only: get and list only, no mutations.
  read_only(model, pk="id", pk_type="uuid", db_key=null, require_auth=false):: {
    model: model,
    pk: pk,
    pk_type: pk_type,
    [if db_key != null then "db_key"]: db_key,
    require_auth: require_auth,
    get: true,
    list: true,
    create: false,
    update: false,
    delete: false,
    actions: [],
  },

  // Write-only: create, update, delete — no read/list endpoints.
  write_only(model, pk="id", pk_type="uuid", db_key=null, require_auth=true):: {
    model: model,
    pk: pk,
    pk_type: pk_type,
    [if db_key != null then "db_key"]: db_key,
    require_auth: require_auth,
    get: false,
    list: false,
    create: true,
    update: true,
    delete: true,
    actions: [],
  },

  // Shorthand for an action entry in the actions list.
  // fn is a dotted Python import path, e.g. "myapp.actions.publish".
  // The callable receives (pk, db=db, **params).
  action(name, fn, params=[], require_auth=true):: {
    name: name,
    fn: fn,
    params: params,
    require_auth: require_auth,
  },
}
