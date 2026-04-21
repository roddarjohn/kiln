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
  action(name, fn, require_auth=true):: {
    name: name,
    fn: fn,
    [if require_auth != true then "require_auth"]: require_auth,
  },
}
