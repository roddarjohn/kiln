// fe/main.libsonnet
//
// Top-level convenience helpers for the fe target's jsonnet
// configuration.  Re-exports the small per-concern helpers so a
// consumer can write `local fe = import "fe/main.libsonnet";`
// once and reach everything via `fe.shell(...)`, `fe.nav.item(...)`,
// `fe.resource(...)`, etc.
//
// Usage:
//   local fe = import "fe/main.libsonnet";
//
//   {
//     openapi_spec: "../be/openapi.json",
//     shell: fe.shell({ brand: "Acme", nav: [...] }),
//     auth:  fe.auth({ ... }),
//     resources: { project: fe.resource({ ... }) },
//   }

local nav     = import "fe/nav.libsonnet";
local presets = import "fe/resources/presets.libsonnet";

{
  // Re-export sub-namespaces.
  nav:: nav,

  // -- Shell ------------------------------------------------------
  //
  // Wrap the consumer's options with the shape the Pydantic schema
  // expects.  Today this is identity, but having the helper means
  // we can add defaulting / validation later without breaking
  // user configs.
  shell(opts):: opts,

  // -- Auth -------------------------------------------------------
  //
  // ``opts`` must include ``login_fn``, ``validate_fn``, and
  // ``logout_fn`` -- those are the openapi-ts SDK function names
  // the codegen wires the AuthProvider callbacks to.
  auth(opts):: opts,

  // -- Resource ---------------------------------------------------
  //
  // Identity wrapper today; lets us add convenience defaulting
  // (e.g. inferring create_request_type from list_item_type) later.
  resource(opts):: opts,

  // -- List view -------------------------------------------------
  list(opts):: opts,

  // -- Filter ----------------------------------------------------
  //
  // Shorthand for declaring a list-view filter.  ``field`` matches
  // the list-fn request body's filter.field literal; ``type``
  // picks the control (text|boolean|select); ``op`` overrides
  // the BE operator (default: contains for text, eq for everything
  // else).
  filter(field, type="text", label=null, op=null, options=[]):: {
    field: field,
    type: type,
    [if label != null then "label"]: label,
    [if op != null then "op"]: op,
    [if std.length(options) > 0 then "options"]: options,
  },

  // -- Detail view -----------------------------------------------
  detail(opts):: opts,

  // -- Detail section --------------------------------------------
  //
  // Either a fields-list section (for label/value pairs from the
  // resource type) or a custom-component section (relative import
  // path; receives the resource as ``item`` prop).
  section(title=null, fields=[], component=null):: {
    [if title != null then "title"]: title,
    [if std.length(fields) > 0 then "fields"]: fields,
    [if component != null then "component"]: component,
  },

  // -- Form -------------------------------------------------------
  form(opts):: opts,

  // -- Action -----------------------------------------------------
  action(opts):: opts,

  // -- Resource preset shortcuts ---------------------------------
  presets:: presets,

  // Convenience: a column entry.  Either a bare field name string
  // (which jsonnet+pydantic accepts as ``{field: name}`` via the
  // schema's ``ColumnSpec`` defaults) or an explicit object with
  // a custom ``label`` / ``display`` / ``sortable``.
  column(field, label=null, display="text", sortable=false):: {
    field: field,
    [if label != null then "label"]: label,
    [if display != "text" then "display"]: display,
    [if sortable then "sortable"]: true,
  },

  // Resource label shortcut.
  label(singular, plural):: { singular: singular, plural: plural },
}
