// be/resources/presets.libsonnet
//
// Helpers for common resource configurations.
//
// Usage:
//   local resource = import "be/resources/presets.libsonnet";
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
  // Default field list for the ``files()`` preset's ``get`` op --
  // mirrors the columns FileMixin supplies.  Pulled out so callers
  // who want to expose extra columns can append to it rather than
  // re-typing the standard set.
  file_fields:: [
    { name: "id", type: "uuid" },
    { name: "s3_key", type: "str" },
    { name: "content_type", type: "str" },
    { name: "size_bytes", type: "int" },
    { name: "original_filename", type: "str" },
    { name: "created_at", type: "datetime" },
    { name: "uploaded_at", type: "datetime" },
  ],

  // Shorthand for an action entry in the operations list.
  //
  // ``fn`` is a dotted Python import path, e.g.
  // ``"myapp.actions.publish"``.  The callable's type annotations are
  // inspected at generation time to determine the request body and
  // response model.
  //
  // ``type: "action"`` tells the engine to dispatch this entry to the
  // Action op regardless of ``name`` (actions have user-defined names).
  //
  // ``status_code`` overrides the response status (e.g. 202 for
  // async-accepted, 201 for create-style).  Unset leaves the
  // framework default: 204 for ``-> None`` actions, 200 otherwise.
  action(name, fn, require_auth=true, status_code=null):: {
    type: "action",
    name: name,
    fn: fn,
    [if require_auth != null then "require_auth"]: require_auth,
    [if status_code != null then "status_code"]: status_code,
  },

  // Bundle a get + the four file-flow actions onto a resource.
  //
  // The consumer creates a File model on their own ``Base`` once
  // per app:
  //
  //   # myapp/models.py
  //   from ingot.files import bind_file_model
  //   from myapp.db import Base
  //
  //   File = bind_file_model(Base)
  //
  // and then in their config:
  //
  //   resources: [
  //     {
  //       model: "myapp.models.File",
  //       pk: "id", pk_type: "uuid",
  //       operations: resource.files(),
  //     },
  //   ],
  //
  // Pass ``fields`` to override the get-op field list (default is
  // the FileMixin column set in ``$.file_fields``); pass
  // ``include_get=false`` to skip the get entirely (e.g. when the
  // consumer wants to define their own with extra columns).
  //
  // Routes generated (relative to the resource prefix):
  //   GET  /{pk}                -- get (FileMixin columns)
  //   POST /upload              -- request_upload (collection)
  //   POST /{pk}/complete       -- complete_upload (object; 204)
  //   POST /{pk}/download       -- download (object; POST is a
  //                               limitation of actions today --
  //                               the response is the GET URL)
  //   POST /{pk}/delete-file    -- delete_file (object; cascades
  //                               S3 + row delete; 204)
  // Default field list for the ``saved_views()`` preset's read
  // ops — mirrors the columns
  // :class:`ingot.saved_views.SavedViewMixin` supplies.
  saved_view_fields:: [
    { name: "id", type: "str" },
    { name: "resource_type", type: "str" },
    { name: "name", type: "str" },
  ],

  // Bundle the five CRUD ops for a SavedView resource.
  //
  // The consumer subclasses
  // :class:`ingot.saved_views.SavedViewMixin` on their own
  // ``DeclarativeBase`` once per app, points a kiln resource at
  // it, and uses this preset to populate ``operations``:
  //
  //   resources: [
  //     {
  //       model: "myapp.models.SavedView",
  //       pk: "id", pk_type: "str",
  //       require_auth: true,
  //       operations: resource.saved_views(
  //         serializer="myapp.serializers.dump_view_hydrated",
  //         owner_guard="myapp.guards.is_view_owner",
  //       ),
  //     },
  //   ],
  //
  // ``serializer`` is a dotted path to an
  // ``async (obj, session, db) -> dict[str, Any]`` function that
  // calls :func:`ingot.saved_views.hydrate_view`.  It runs on the
  // get + list reads so stored ``ref`` filter ids hydrate to
  // ``items`` automatically.
  //
  // ``owner_guard`` is a dotted path to an
  // ``async (resource, session) -> bool`` guard used as the
  // ``can`` callable on every op except ``create`` (the row
  // doesn't exist yet at that point); typically checks
  // ``resource.owner_id == str(session.user_id)``.
  //
  // ``fields`` overrides the field list on the read ops.
  saved_views(
    serializer,
    owner_guard,
    fields=null,
    create_fields=null,
    update_fields=null,
    filter_fields=null,
  )::
    local read_fields =
      if fields != null then fields else $.saved_view_fields;
    local write_fields =
      if create_fields != null then create_fields
      else [{ name: "name", type: "str" }];
    local upd_fields =
      if update_fields != null then update_fields
      else [{ name: "name", type: "str" }];
    local f_fields =
      if filter_fields != null then filter_fields
      else [{ name: "resource_type", values: "free_text" }];
    [
      {
        name: "get",
        fields: read_fields,
        serializer: serializer,
        can: owner_guard,
      },
      {
        name: "list",
        fields: read_fields,
        serializer: serializer,
        can: owner_guard,
        modifiers: [
          { type: "filter", fields: f_fields },
        ],
      },
      {
        name: "create",
        fields: write_fields,
      },
      {
        name: "update",
        fields: upd_fields,
        can: owner_guard,
      },
      {
        name: "delete",
        can: owner_guard,
      },
    ],

  files(
    require_auth=true,
    fields=null,
    include_get=true,
  )::
    (
      if include_get then [
        {
          name: "get",
          fields: if fields != null then fields else $.file_fields,
          [if require_auth != null then "require_auth"]: require_auth,
        },
      ] else []
    ) + [
      $.action(
        name="upload",
        fn="ingot.files.request_upload",
        require_auth=require_auth,
      ),
      $.action(
        name="complete",
        fn="ingot.files.complete_upload",
        require_auth=require_auth,
      ),
      $.action(
        name="download",
        fn="ingot.files.download",
        require_auth=require_auth,
      ),
      $.action(
        name="delete_file",
        fn="ingot.files.delete_file",
        require_auth=require_auth,
      ),
    ],
}
