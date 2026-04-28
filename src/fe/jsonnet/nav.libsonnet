// fe/nav.libsonnet
//
// Sidebar navigation helpers.
//
// Usage:
//   local fe = import "fe/main.libsonnet";
//
//   shell: fe.shell({
//     nav: [
//       fe.nav.item("Projects", view="projects"),
//       fe.nav.item("Tasks",    view="tasks"),
//     ],
//   })

{
  // A single nav entry.  ``view`` should match a key in
  // :attr:`ProjectConfig.resources` to render that resource's
  // list page, or be an arbitrary identifier you wire to a
  // custom view in your own ``App.tsx`` overlay.
  item(label, view):: { label: label, view: view },
}
