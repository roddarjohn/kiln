// kiln stdlib — CRUD preset helpers
// Usage: local crud = import 'kiln/crud/presets.libsonnet';
//        crud.full({ require_auth: ["update", "delete"] })
local defaults = {
  create: true,
  read: true,
  update: true,
  delete: true,
  list: true,
  paginated: true,
  require_auth: [],
};

{
  // All five operations enabled.
  full(opts={}):: defaults + opts,

  // Read and list only — no write operations.
  read_only(opts={}):: defaults + {
    create: false,
    update: false,
    delete: false,
  } + opts,

  // All operations but no list endpoint.
  no_list(opts={}):: defaults + { list: false } + opts,

  // Write-only — create/update/delete, no read or list.
  write_only(opts={}):: defaults + {
    read: false,
    list: false,
  } + opts,
}
