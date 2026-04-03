// kiln stdlib — pgcraft factory aliases
//
// Usage:
//   local factories = import 'kiln/pgcraft/factories.libsonnet';
//   pgcraft_type: factories.simple
//   pgcraft_type: factories.append_only
{
  // Standard dimension table.
  simple: "pgcraft.factory.dimension.simple.PGCraftSimple",

  // Immutable append-only ledger — no update or delete at the DB level.
  append_only: "pgcraft.factory.dimension.append_only.PGCraftAppendOnly",

  // Double-entry ledger with balance tracking.
  ledger: "pgcraft.factory.ledger.PGCraftLedger",

  // Entity–attribute–value table.
  eav: "pgcraft.factory.dimension.eav.PGCraftEAV",
}
