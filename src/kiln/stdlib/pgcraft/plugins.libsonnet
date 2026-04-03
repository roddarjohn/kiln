// kiln stdlib — pgcraft plugin helpers
//
// Auto-generated from pgcraft introspection — do not edit by hand.
// Re-generate with: just generate-pgcraft-stdlib
//
// Usage:
//   local plugins = import 'kiln/pgcraft/plugins.libsonnet';
//
//   // PK plugin (use as primary_key value on a field):
//   field.uuid('id', primary_key=plugins.pk.uuid_v4)
//   field.uuid('id', primary_key=plugins.pk.uuid_v7)
//
//   // No-arg plugin (use in pgcraft_plugins list):
//   pgcraft_plugins: [plugins.check]
//
//   // Configurable plugin (use in pgcraft_plugins list):
//   pgcraft_plugins: [plugins.postgrest({ grants: ['select'] })]
{
  // ---------------------------------------------------------------------
  // PK plugins — use as primary_key on uuid/int fields.
  // column_name is derived from the field name automatically.
  // ---------------------------------------------------------------------
  pk: {
    uuid_v4: "pgcraft.plugins.pk.UUIDV4PKPlugin",
    uuid_v7: "pgcraft.plugins.pk.UUIDV7PKPlugin",
    serial: "pgcraft.plugins.pk.SerialPKPlugin",
  },

  // ---------------------------------------------------------------------
  // No-arg plugins — pass as strings in pgcraft_plugins.
  // ---------------------------------------------------------------------
  // Materialize :class:`~pgcraft.check.PGCraftCheck` as table constraints.
  check: "pgcraft.plugins.check.TableCheckPlugin",
  // Materialize FK declarations as ``ForeignKeyConstraint`` objects.
  fk: "pgcraft.plugins.fk.TableFKPlugin",
  // Materialize :class:`~pgcraft.index.PGCraftIndex` as table indexes.
  index: "pgcraft.plugins.index.TableIndexPlugin",
  // Register an AFTER INSERT trigger enforcing balanced entries.
  double_entry_trigger: "pgcraft.plugins.ledger.DoubleEntryTriggerPlugin",

  // ---------------------------------------------------------------------
  // Configurable plugins — call as functions in pgcraft_plugins.
  // ---------------------------------------------------------------------
  // Create a PostgREST-facing view, register its grants and triggers.
  postgrest(opts={}):: {
    path: "pgcraft.extensions.postgrest.plugin.PostgRESTPlugin",
    args: {
            schema: std.get(opts, "schema", "api"),
          }
          + (if "grants" in opts then { grants: opts.grants } else {})
          + (if "columns" in opts then { columns: opts.columns } else {})
          + (if "exclude_columns" in opts then { exclude_columns: opts.exclude_columns } else {}),
  },
  // Provide the ``created_at`` column name for table plugins.
  created_at(opts={}):: {
    path: "pgcraft.plugins.created_at.CreatedAtPlugin",
    args: {
      column_name: std.get(opts, "column_name", "created_at"),
    },
  },
  // Add debit/credit semantics to a ledger table.
  double_entry(opts={}):: {
    path: "pgcraft.plugins.ledger.DoubleEntryPlugin",
    args: {
      column_name: std.get(opts, "column_name", "direction"),
    },
  },
  // Enforce a minimum balance per dimension group.
  ledger_balance_check(opts={}):: {
    path: "pgcraft.plugins.ledger.LedgerBalanceCheckPlugin",
    args: {
      dimensions: opts.dimensions,
      min_balance: std.get(opts, "min_balance", 0),
    },
  },
}
