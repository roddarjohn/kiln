// kiln/resources/presets.libsonnet
//
// Helpers for common resource configurations.
//
// Usage:
//   local resource = import "kiln/resources/presets.libsonnet";
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
  // Default field list for the ``documents()`` preset's ``get`` op
  // -- mirrors the columns DocumentMixin supplies.  Pulled out so
  // callers who want to expose extra columns can append to it
  // rather than re-typing the standard set.
  document_fields:: [
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

  // Bundle a get + the four document-flow actions onto a resource.
  //
  // The consumer creates a Document model on their own ``Base``
  // once per app:
  //
  //   # myapp/models.py
  //   from ingot.documents import bind_document_model
  //   from myapp.db import Base
  //
  //   Document = bind_document_model(Base)
  //
  // and then in their config:
  //
  //   resources: [
  //     {
  //       model: "myapp.models.Document",
  //       pk: "id", pk_type: "uuid",
  //       operations: resource.documents(),
  //     },
  //   ],
  //
  // Pass ``fields`` to override the get-op field list (default is
  // the DocumentMixin column set in ``$.document_fields``); pass
  // ``include_get=false`` to skip the get entirely (e.g. when the
  // consumer wants to define their own with extra columns).
  //
  // Routes generated (relative to the resource prefix):
  //   GET  /{pk}                    -- get (DocumentMixin columns)
  //   POST /upload                  -- request_upload (collection)
  //   POST /{pk}/complete           -- complete_upload (object; 204)
  //   POST /{pk}/download           -- download (object; POST is a
  //                                   limitation of actions today --
  //                                   the response is the GET URL)
  //   POST /{pk}/delete-document    -- delete_document (object;
  //                                   cascades S3 + row delete; 204)
  documents(
    require_auth=true,
    fields=null,
    include_get=true,
  )::
    (
      if include_get then [
        {
          name: "get",
          fields: if fields != null then fields else $.document_fields,
          [if require_auth != null then "require_auth"]: require_auth,
        },
      ] else []
    ) + [
      $.action(
        name="upload",
        fn="ingot.documents.request_upload",
        require_auth=require_auth,
      ),
      $.action(
        name="complete",
        fn="ingot.documents.complete_upload",
        require_auth=require_auth,
      ),
      $.action(
        name="download",
        fn="ingot.documents.download",
        require_auth=require_auth,
      ),
      $.action(
        name="delete_document",
        fn="ingot.documents.delete_document",
        require_auth=require_auth,
      ),
    ],
}
