# Generic Filtering & Saved Views — Plan

Status: draft, pre-implementation. Locks the contracts before any
code lands in `be`.

## Goals

A generic, opt-in primitive on the `be` plugin for:

1. **Filter discovery** — `GET /{resource}/_filters` returns
   metadata: what fields are filterable, what operators each
   supports, what's sortable, where to fetch valid values.
   `GET /{resource}/_filters/{field}` returns just one filter for
   lazy-rendered UI. (Filter *execution* uses the existing
   `POST /{resource}/search` endpoint; this plan enriches the
   structured filter spec the SearchRequest already carries
   rather than introducing a parallel execution path.)
2. **BE-powered value providers** — uniform `POST` endpoints for
   populating filter inputs (enums, bools, FK lookups, free-text
   searches), pageable and query-narrowable.
3. **Per-resource search endpoint** — `POST /{resource}/_values`
   with `{q, cursor?}`, returning `{type, id, label}` results matched
   against the resource's configured search fields. Powers `ref`
   filter inputs on other resources and any FE "search this table"
   affordance.
4. **Saved views** — per-user CRUD of named filter+sort states, with
   a dump format that resolves typed references (e.g.
   `Customer #123 → "Acme Corp"`) via a per-resource labeler.

All endpoints with non-trivial inputs are `POST` with JSON bodies.
Only metadata GETs (`GET /_filters`) and entity GETs
(`GET /views/{id}`) stay GET. No filter/search state in URLPlease go t params.

## Non-goals (initial cut)

- **FE codegen for filter components.** The discovery endpoint's
  consumer is hand-written for now; the `fe` plugin doesn't generate
  UI from it. Revisit later.
- **OR-group / nested filter trees.** AND across fields only.
  Simpler payload, easier saved-view dump, easier permission story.
- **Cross-resource / cmd-K-style global search.** Out of scope here.
  Each resource gets its own `/_values?q=` search; aggregating them
  for a command-palette UI is a separate effort that can fan out to
  these endpoints if it wants to.
- **Relevance ranking inside `_values?q=`.** OR across the
  resource's configured search fields, ordered by lexical hit count.
  No tf-idf, no weighting.
- **Saved-view sharing / teams.** Per-user only at first.

## Surface (jsonnet)

### Resource-level opt-ins

Mirror the `permissions_endpoint: true` idiom from #60. New booleans
on `ResourceConfig`:

```jsonnet
{
  model: "inventory.models.Product",
  // ...
  filter_discovery: true,         // emit GET /products/_filters
  searchable: true,               // emit POST /products/_values
  saved_views: true,              // emit /products/views CRUD
  link: { kind: "id_name", name: "name" },
  // ...
}
```

`link` declares how the resource serializes when it appears as a
search result, ref filter value, or saved-view item. Required when
`saved_views` or `searchable` is on, and on any resource that's a
`ref_resource` target of another resource's filter. A compile-time
validator (alongside the existing #60 permission validator in
`src/be/config/schema.py`) rejects the opt-in without it.

### Link schemas

A small set of built-in Pydantic schemas covers the common shapes;
the FE switches on `type` and renders accordingly. The BE never
assembles display strings — it returns structured fields.

| `kind:` | Pydantic schema | Use |
|---|---|---|
| `name` | `LinkName{name}` | label-only (rare) |
| `id` | `LinkID{id}` | id-only (rare) |
| `id_name` | `LinkIDName{id, name}` | default for most resources |

Each `link:` block takes the schema kind plus either a field
shorthand or a builder callable:

```jsonnet
// Shorthand: pulls fields directly off the model.
link: { kind: "id_name", name: "number" }

// Builder: arbitrary logic, returns the schema instance.
link: { kind: "id_name",
        builder: "inventory.labels.order_link" }
```

The builder signature is
`async (instance, session) -> LinkIDName` (or whichever schema the
`kind:` declares). Session is for permission-aware redaction —
most builders won't use it, but it's there for symmetry with `can`
guards.

```python
# inventory/labels.py
from be.links import LinkIDName

async def order_link(order: Order, session: Session) -> LinkIDName:
    return LinkIDName(id=order.id, name=f"#{order.number}")
```

Adding a new schema (e.g. `LinkIDNameSubtitle{id, name, subtitle}`)
when a richer shape is wanted is a one-time addition to the
built-in set; resources opt in by `kind:`.

### Per-operation extensions to `filter:` and `order:`

`list.searchable(...)` already takes
`filter: { fields: ["sku", "name"] }`. Extend it without breaking
back-compat:

```jsonnet
list.searchable(
  fields: [...],
  filter: {
    fields: [
      { name: "status", operators: ["eq", "in"],
        values: "enum", enum: "inventory.models.OrderStatus" },
      { name: "is_archived", operators: ["eq"], values: "bool" },
      { name: "customer_id", operators: ["eq", "in"],
        values: "ref", ref_resource: "customer" },
      { name: "sku", operators: ["eq", "contains"],
        values: "free_text" },
      { name: "created_at", operators: ["gte", "lte"],
        values: "literal", type: "datetime" },
      "name",  // shorthand: {operators: ["contains"],
               //            values: "free_text"}
    ],
  },
  order: { fields: ["name", "created_at"], default: "name" },
  search: {
    fields: ["sku", "name"],  // OR-matched by POST /_values
  },
  paginate: { mode: "keyset", cursor_field: "id" },
)
```

The string-shorthand stays valid (back-compat with existing
fixtures); the structured form is required to express operators or
non-`free_text` value sources.

### `values:` modes

The discriminator that drives value-provider routing and FE
rendering:

| `values:` | Meaning | Discovery carries | Has POST endpoint? |
|---|---|---|---|
| `enum` | Points at a Python `Enum` (`enum: "..."`) | inline `choices: [...]` | yes (uniform) |
| `bool` | Special — FE renders toggle/checkbox | nothing extra | no |
| `ref` | FK to another resource | `ref_resource` | yes — that resource's `_values` |
| `free_text` | String search over the column | `endpoint: "/_values/{field}"` | yes |
| `literal` | Numeric / date — FE renders native input | `type: "..."` | no |

`enum` choices are inlined in `GET /_filters` so a typical dropdown
needs no extra roundtrip. The uniform `POST /_values/{field}` is
still available for any field, useful if an enum grows large enough
to want pagination/search later.

`bool` is intentionally not just an enum of `[true, false]` — the
FE renders bools differently (toggle vs dropdown), so the kind is
distinct.

## Discovery payload

`GET /{resource}/_filters` returns metadata for every filterable
field, with `enum` and `bool` choices inlined so simple dropdowns
need zero further roundtrips:

```json
{
  "filters": [
    {
      "field": "status",
      "operators": ["eq", "in"],
      "values": {
        "kind": "enum",
        "choices": [
          {"value": "open", "label": "Open"},
          {"value": "fulfilled", "label": "Fulfilled"},
          {"value": "cancelled", "label": "Cancelled"}
        ],
        "endpoint": "/products/_filters/status"
      }
    },
    {
      "field": "is_archived",
      "operators": ["eq"],
      "values": {"kind": "bool"}
    },
    {
      "field": "customer_id",
      "operators": ["eq", "in"],
      "values": {
        "kind": "ref",
        "type": "customer",
        "endpoint": "/customers/_values"
      }
    },
    {
      "field": "sku",
      "operators": ["eq", "contains"],
      "values": {
        "kind": "free_text",
        "endpoint": "/products/_values/sku"
      }
    },
    {
      "field": "created_at",
      "operators": ["gte", "lte"],
      "values": {"kind": "literal", "type": "datetime"}
    }
  ],
  "sort": {
    "fields": ["name", "created_at"],
    "default": "name"
  }
}
```

`GET /{resource}/_filters/{field}` returns the same shape for a
single filter — useful when a UI lazily renders one filter at a
time and doesn't want the whole discovery payload.

Permission gate: same `require_auth` / `can` machinery as the
underlying list op. If a user can't list the resource, they can't
see its filter shape.

## Filter execution

The existing `POST /{resource}/search` endpoint (generated by the
`list` op + its `filter`/`order`/`paginate` modifiers) is the
execution path. This plan enriches the SearchRequest body's
`FilterCondition` shape so the same operators / value kinds that
discovery describes can actually be sent. Body shape (additive):

```json
{
  "filters": [
    {"field": "status", "op": "in", "value": ["open", "fulfilled"]},
    {"field": "is_archived", "op": "eq", "value": false},
    {"field": "customer_id", "op": "in",
     "value": ["01H...", "01J..."]},
    {"field": "created_at", "op": "gte",
     "value": "2026-01-01T00:00:00Z"}
  ],
  "sort": {"field": "created_at", "direction": "desc"},
  "cursor": null,
  "limit": 50
}
```

AND across `filters` entries; multi-value matches use `in`.

## Value-provider protocol

Uniform endpoint shape for both per-field providers and the
resource-level search:

```
POST /{resource}/_values             # resource-level search
POST /{resource}/_values/{field}     # per-field values
```

Body:

```json
{"q": "acm", "cursor": null, "limit": 50}
```

Response (resource-level / `ref` field — items conform to the
target resource's link schema):

```json
{
  "results": [
    {"type": "customer", "id": "01H...", "name": "Acme Corp"},
    {"type": "customer", "id": "01J...", "name": "Beta LLC"}
  ],
  "next_cursor": "..."
}
```

For `enum` fields, items carry the enum value alongside its label
(no link schema involved):

```json
{
  "results": [
    {"value": "open", "label": "Open"},
    {"value": "fulfilled", "label": "Fulfilled"}
  ]
}
```

- `type` is the resource name on resource-typed results, omitted
  on enum results.
- `bool` and `literal` modes have no `_values` endpoint — they're
  rendered natively from the discovery payload.
- Permission gating runs through the target resource's
  list-permission gate. **A value provider on `customer_id` filters
  the dropdown to customers the user is allowed to see.** Wire
  through the #60 guard machinery.

## Saved views

### Endpoints

Per-resource CRUD: `GET|POST|PATCH|DELETE /{resource}/views[/{id}]`.
Per-user scoping enforced at the operation level (rows filtered by
`owner_id == session.user_id`). Sharing/teams: deferred.

### Stored payload (DB)

```json
{
  "name": "Open orders, recent",
  "filters": [
    {"field": "status", "op": "eq",
     "value": {"kind": "literal", "value": "open"}},
    {"field": "customer_id", "op": "in",
     "value": {"kind": "ref", "type": "customer",
               "ids": ["01H...", "01J..."]}}
  ],
  "sort": {"field": "created_at", "direction": "desc"}
}
```

The stored form holds **raw IDs only** — no labels are snapshotted.
Decision: resolve on read, never on write. Rationale: customer
renames stay in sync. Cost: a labeler call per ref on view fetch;
cheap because it's a labeler, not a full serializer.

### Dump format (read response)

When a saved view is fetched, each `ref` value is hydrated through
the target resource's link builder. Items conform to that
resource's link schema:

```json
{
  "id": "view_01H...",
  "name": "Open orders, recent",
  "filters": [
    {"field": "status", "op": "eq",
     "value": {"kind": "literal", "value": "open"}},
    {"field": "customer_id", "op": "in",
     "value": {"kind": "ref", "type": "customer",
               "items": [
                 {"id": "01H...", "name": "Acme Corp"},
                 {"id": "01J...", "name": "Beta LLC"}
               ],
               "dropped": 0}}
  ],
  "sort": {"field": "created_at", "direction": "desc"}
}
```

If a referenced row no longer exists or the user can't see it, the
item is dropped from `items` and `dropped` is incremented. Saved
views never error because of stale or invisible refs.

## Custom kiln (what's Python, what's jsonnet)

Following the `src/be/operations/permissions.py` and
`src/be/operations/actions.py` precedent:

- **Jsonnet** — declarative surface only (`filter_discovery: true`,
  the structured `filter:` block, `labeler:` dotted path). No
  logic.
- **Python operations** under `src/be/operations/`:
  - `filter_discovery.py` —
    `@operation("filter_discovery", scope="operation")` with
    `when()` gating on `filter_discovery: true`. Generates the
    `GET /_filters`, `GET /_filters/{field}`, and
    `POST /_filters` routes from the structured filter block.
    Reads `enum:` dotted paths at codegen time to inline choices.
  - `value_providers.py` —
    `@operation("value_providers", scope="resource")`. Emits
    `POST /_values/{field}` for each `enum` and `free_text` field,
    plus `POST /_values` for resource-level search when
    `searchable: true`. Wires each to either an enum-backed list,
    a free-text DB query, or a delegation to a ref'd resource's
    value provider.
  - `saved_views.py` —
    `@operation("saved_views", scope="resource")` with `when()` on
    `saved_views: true`. Generates the views table model, CRUD
    routes, and the read-time labeler resolution. Requires
    `labeler` set; compile-time validator rejects otherwise.
- **Per-app registry file** — generated alongside `actions.py`:
  `links.py` with `LINKS: dict[str, LinkBuilder]` keyed by
  resource name, populated from each resource's `link:` block
  (either the field-shorthand-generated builder or the dotted-path
  builder). Saved-view dump and `_values` endpoints both look up
  link builders through this dict.

## Sequencing

1. **Filter discovery + execution + value providers** — `enum` /
   `bool` / `free_text` / `literal` modes. No `ref`, no
   cross-resource. Load-bearing primitive.
2. **`ref` value mode + labeler registry.** Adds the cross-resource
   piece. Labeler required on any resource a `ref` filter can
   target. `searchable: true` on those resources.
3. **Saved views.** Now that ref + labeler exist, the dump format
   is straightforward.

Each step is independently shippable and testable.

## Open questions

- **Operator vocabulary.** Initial set: `eq`, `in`, `contains`,
  `gte`, `lte`, `is_null`. Add `not_in` / `not_eq` only when asked.
- **Sort direction syntax.** `?sort=name,-created_at` (Django-ish)
  vs `?sort=name:asc,created_at:desc`. Lean toward the second for
  readability.
- **Link builder signature.** `async (instance, session) ->
  LinkSchema`, matching the `can` guard signature for symmetry.
  Whether session is actually used by builders is up to the user;
  it's there for permission-aware redaction.
- **Built-in schema set.** `LinkName`, `LinkID`, `LinkIDName` to
  start. Add `LinkIDNameSubtitle` (or similar) when a resource
  needs richer rendering — extending the set is cheap, but adding
  one per resource defeats the point.
- **Where the saved-views table lives.** One shared `saved_views`
  table with a `resource_type` column, or one per resource.
  Shared is simpler and matches the per-user scope; per-resource
  matches the routing idiom but multiplies migrations. Lean toward
  shared.
- **Index hints in codegen.** Filterable + sortable columns are
  prime candidates for indexes. Worth nudging users (warning at
  codegen if a filterable column has no index in the model)? Not
  blocking v1.
