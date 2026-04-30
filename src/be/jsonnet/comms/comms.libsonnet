// be stdlib — communication-platform config helpers
// Usage:
//   local comms = import 'be/comms/comms.libsonnet';
//   {
//     comms: comms.platform({
//       message_model:   'myapp.models.CommMessage',
//       recipient_model: 'myapp.models.CommRecipient',
//       transports: {
//         email: 'myapp.comms.transports.email_transport',
//         sms:   'myapp.comms.transports.sms_transport',
//       },
//       preferences: 'myapp.comms.prefs.resolver',
//       types: [
//         comms.type({
//           name: 'order_shipped',
//           context_schema: 'myapp.comms.contexts.OrderShipped',
//           subject_template: 'Order {{ order_id }} shipped',
//           body_template: comms.path('templates/order_shipped.html'),
//           default_methods: ['email', 'sms'],
//         }),
//       ],
//     }),
//   }
//
// Fields mirror be.config.schema.CommsConfig and CommTypeConfig.
// Optional keys (renderer, preferences, db_key, default_methods,
// subject_template) are only emitted when set, so missing keys
// inherit the schema defaults.
//
// `comms.path(p)` marks a template value as a file path: the file
// is read at scaffold time and its contents inlined into the
// generated module as a string literal.  Paths are relative to the
// directory in which `foundry generate` is invoked, or absolute.
{
  platform(opts):: {
    message_model: opts.message_model,
    recipient_model: opts.recipient_model,
    types: std.get(opts, "types", []),
    transports: std.get(opts, "transports", {}),
    [if std.objectHas(opts, "renderer") then "renderer"]:
      opts.renderer,
    [if std.objectHas(opts, "preferences") then "preferences"]:
      opts.preferences,
    [if std.objectHas(opts, "db_key") then "db_key"]: opts.db_key,
  },

  type(opts):: {
    name: opts.name,
    context_schema: opts.context_schema,
    [if std.objectHas(opts, "subject_template") then "subject_template"]:
      opts.subject_template,
    body_template: opts.body_template,
    [if std.objectHas(opts, "default_methods") then "default_methods"]:
      opts.default_methods,
  },

  // Marker indicating a template should be read from a file at
  // scaffold time rather than treated as inline source.  Resolves
  // to be.config.schema.TemplateSource on the Python side.
  path(p):: { path: p },
}
