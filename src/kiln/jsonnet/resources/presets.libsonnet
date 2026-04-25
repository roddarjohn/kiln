// kiln/resources/presets.libsonnet
//
// Helpers for common resource configurations.
//
// Usage:
//   local resource = import "kiln/resources/presets.libsonnet";
//
//   resources: [
//     {
//       model: "myapp.models.Article",
//       operations: [
//         { name: "get", fields: [...] },
//         resource.action(name="publish", fn="myapp.actions.publish"),
//       ],
//     },
//   ],

{
  // Shorthand for an action entry in the operations list.
  //
  // ``fn`` is a dotted Python import path, e.g.
  // ``"myapp.actions.publish"``.  The callable's type annotations are
  // inspected at generation time to determine the request body and
  // response model.
  //
  // ``type: "action"`` tells the engine to dispatch this entry to the
  // Action op regardless of ``name`` (actions have user-defined names).
  action(name, fn, require_auth=true):: {
    type: "action",
    name: name,
    fn: fn,
    [if require_auth != null then "require_auth"]: require_auth,
  },
}
