// kiln/fields.libsonnet
//
// Helpers for common field-list bundles.
//
// Usage:
//   local fields = import "kiln/fields.libsonnet";
//
//   operations: [
//     {
//       name: "get",
//       fields: [
//         fields.id(),
//         { name: "title", type: "str" },
//         fields.nested("project", "blog.models.Project", [
//           fields.id(),
//           { name: "name", type: "str" },
//         ]),
//       ],
//     },
//   ]

{
  // Primary-key field.  Defaults to uuid; pass "int" for integer PKs.
  id(type="uuid"):: { name: "id", type: type },

  // Standard created_at / updated_at pair.
  timestamps():: [
    { name: "created_at", type: "datetime" },
    { name: "updated_at", type: "datetime" },
  ],

  // Nested dump of a related model.
  //
  // ``model`` is the dotted import path of the related SQLAlchemy
  // class.  ``fields`` is the sub-field list (can itself contain
  // further nested entries).  Pass ``many=true`` when the
  // relationship returns a collection.
  nested(name, model, fields, many=false):: {
    name: name,
    type: "nested",
    model: model,
    fields: fields,
    [if many then "many"]: true,
  },
}
