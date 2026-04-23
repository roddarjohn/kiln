// kiln/operations/list.libsonnet
//
// Bundles a list op with its optional filter / order / paginate
// extensions into the four `operations` entries the engine wants,
// so users can declare search-capable lists in one place.
//
// Usage:
//   local list = import "kiln/operations/list.libsonnet";
//
//   operations: list.searchable(
//     fields=[...],
//     filter={ fields: ["name", "author"] },
//     order={ fields: ["name"], default: "name" },
//     paginate={ mode: "keyset", default_page_size: 25 },
//   ),
//
// Any of ``filter``, ``order``, ``paginate`` may be omitted; the
// preset emits only the extension entries that are requested.

{
  // Expand into the flat ``operations`` list the engine consumes.
  //
  // Args:
  //   fields:   Field list for the list op's response items.
  //   filter:   Optional FilterConfig dict (e.g. ``{ fields: [...] }``).
  //   order:    Optional OrderConfig dict
  //             (``{ fields: [...], default: ..., default_dir: ... }``).
  //   paginate: Optional PaginateConfig dict
  //             (``{ mode: ..., cursor_field: ..., default_page_size: ... }``).
  searchable(fields, filter=null, order=null, paginate=null):: std.prune(
    [
      { name: "list", fields: fields },
      if filter != null then { type: "filter" } + filter,
      if order != null then { type: "order" } + order,
      if paginate != null then { type: "paginate" } + paginate,
    ],
  ),
}
