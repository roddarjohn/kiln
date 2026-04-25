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
  // Shorthand for an action entry in the operations list.
  //
  // ``fn`` is a dotted Python import path, e.g.
  // ``"myapp.actions.publish"``.  The callable's type annotations are
  // inspected at generation time to determine the request body and
  // response model.
  //
  // ``type: "action"`` tells the engine to dispatch this entry to the
  // Action op regardless of ``name`` (actions have user-defined names).
  action(name, fn, require_auth=true):: {
    type: "action",
    name: name,
    fn: fn,
    [if require_auth != null then "require_auth"]: require_auth,
  },

  // Bundle the four document-flow actions onto a resource.
  //
  // The consumer points ``actions_module`` at a project-local module
  // that re-exports the ingot.documents helpers, binding
  // ``request_upload`` to their concrete model class:
  //
  //   # myapp/attachments/actions.py
  //   from ingot.documents import (
  //     make_request_upload, complete_upload, download, delete_document,
  //   )
  //   from myapp.models import Attachment
  //
  //   request_upload = make_request_upload(Attachment)
  //
  // and then in their config:
  //
  //   resources: [
  //     {
  //       model: "myapp.models.Attachment",
  //       pk: "id", pk_type: "uuid",
  //       operations: [...] + resource.documents("myapp.attachments.actions"),
  //     },
  //   ],
  //
  // Routes generated (relative to the resource prefix):
  //   POST /upload                  -- request_upload (collection)
  //   POST /{pk}/complete           -- complete_upload (object)
  //   POST /{pk}/download           -- download (object; POST is a
  //                                   limitation of actions today --
  //                                   the response is the GET URL)
  //   POST /{pk}/delete-document    -- delete_document (object;
  //                                   cascades S3 delete + row delete)
  documents(actions_module, require_auth=true):: [
    $.action(
      name="upload",
      fn=actions_module + ".request_upload",
      require_auth=require_auth,
    ),
    $.action(
      name="complete",
      fn=actions_module + ".complete_upload",
      require_auth=require_auth,
    ),
    $.action(
      name="download",
      fn=actions_module + ".download",
      require_auth=require_auth,
    ),
    $.action(
      name="delete_document",
      fn=actions_module + ".delete_document",
      require_auth=require_auth,
    ),
  ],
}
