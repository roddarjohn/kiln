// fe/resources/presets.libsonnet
//
// Shorthand resource configurations to keep common patterns
// from getting verbose.  Each preset returns the dict shape the
// :class:`fe.config.ResourceConfig` Pydantic schema expects.
//
// Usage:
//   local fe = import "fe/main.libsonnet";
//
//   resources: {
//     project: fe.presets.crud({
//       label_singular: "Project",
//       label_plural:   "Projects",
//       list_item_type: "ProjectListItem",
//       list_fn:        "listProjectsV1TrackerProjectsSearchPost",
//       create_fn:      "createProjectV1TrackerProjectsPost",
//       delete_fn:      "deleteProjectV1TrackerProjectsIdDelete",
//       create_request_type: "ProjectCreateRequest",
//       columns: ["name", "slug"],
//       create_fields: ["name", "slug", "description"],
//     }),
//   }

{
  // A standard list + create + delete CRUD bundle.
  //
  // ``columns`` is a list of bare field-name strings; for
  // anything richer (custom labels or badge display) drop into
  // a full ``fe.resource(...)`` call and pass column objects.
  //
  // ``create_fields`` declares which body fields the create
  // form exposes -- omit to skip the create surface entirely.
  crud(opts):: {
    label: { singular: opts.label_singular, plural: opts.label_plural },
    list_item_type: opts.list_item_type,
    [if std.objectHas(opts, "list_fn") then "list_fn"]: opts.list_fn,
    [if std.objectHas(opts, "get_fn") then "get_fn"]: opts.get_fn,
    [if std.objectHas(opts, "create_fn") then "create_fn"]: opts.create_fn,
    [if std.objectHas(opts, "update_fn") then "update_fn"]: opts.update_fn,
    [if std.objectHas(opts, "delete_fn") then "delete_fn"]: opts.delete_fn,
    [if std.objectHas(opts, "create_request_type") then "create_request_type"]: opts.create_request_type,
    [if std.objectHas(opts, "update_request_type") then "update_request_type"]: opts.update_request_type,
    [if std.objectHas(opts, "resource_type") then "resource_type"]: opts.resource_type,
    list: {
      columns: [
        if std.isString(c) then { field: c } else c
        for c in std.get(opts, "columns", default=[])
      ],
      toolbar_actions:
        if std.objectHas(opts, "create_fn") then ["create"] else [],
      row_actions:
        if std.objectHas(opts, "delete_fn") then ["delete"] else [],
    },
    [if std.objectHas(opts, "create_fields") then "create"]: {
      fields: std.get(opts, "create_fields", default=[]),
      presentation: std.get(opts, "create_presentation", default="drawer"),
    },
    [if std.objectHas(opts, "update_fields") then "update"]: {
      fields: std.get(opts, "update_fields", default=[]),
      presentation: std.get(opts, "update_presentation", default="drawer"),
    },
    [if std.objectHas(opts, "actions") then "actions"]: opts.actions,
  },
}
