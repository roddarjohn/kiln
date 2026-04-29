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
  // custom view in your own ``App.tsx`` overlay.  ``icon`` is
  // an optional lucide-react icon name (e.g. ``"FolderOpen"``)
  // rendered next to the label.
  item(label, view, icon=null):: (
    { label: label, view: view }
    + (if icon != null then { icon: icon } else {})
  ),
}
