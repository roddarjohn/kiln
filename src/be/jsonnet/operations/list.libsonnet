// be/operations/list.libsonnet
//
// Bundles a list op with its optional filter / order / paginate
// modifiers into the single nested shape the engine consumes.
//
// Usage:
//   local list = import "be/operations/list.libsonnet";
//
//   operations: [
//     list.searchable(
//       fields=[...],
//       filter={ fields: ["name", "author"] },
//       order={ fields: ["name"], default: "name" },
//       paginate={ mode: "keyset", default_page_size: 25 },
//     ),
//     // other ops...
//   ],
//
// Any of ``filter``, ``order``, ``paginate`` may be omitted; the
// preset only emits modifiers for the arguments provided.  The
// result is a single operations entry whose ``modifiers`` list
// nests each requested modifier with its ``type`` discriminator.

{
  // Build one list operation with zero or more modifiers.
  //
  // Args:
  //   fields:   Field list for the list op's response items.
  //   filter:   Optional FilterConfig dict
  //             (``{ fields: [...] }``).
  //   order:    Optional OrderConfig dict
  //             (``{ fields: [...], default: ..., default_dir: ... }``).
  //   paginate: Optional PaginateConfig dict
  //             (``{ mode: ..., cursor: { name: ..., type: ... },
  //              default_page_size: ... }``).
  searchable(fields, filter=null, order=null, paginate=null):: {
    name: "list",
    fields: fields,
    modifiers: std.prune([
      if filter != null then { type: "filter" } + filter,
      if order != null then { type: "order" } + order,
      if paginate != null then { type: "paginate" } + paginate,
    ]),
  },
}
