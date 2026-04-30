Usage
=====

.. contents:: On this page
   :local:
   :depth: 2

This page covers day-to-day usage of the ``foundry`` CLI.  Most of
the page is centred on the ``be`` target since it has the deepest
config surface; the ``be_root`` / ``fe`` / ``fe_root`` targets each
get a short section near the end.  For a walkthrough of a brand-new
project, see :doc:`getting_started`.  For the complete config schema,
see :doc:`reference`.

Install
-------

.. code-block:: bash

   pip install kiln-generator  # or: uv add kiln-generator

Installing ``kiln-generator`` ships the generic ``foundry`` CLI and
all four targets it discovers at startup (``be``, ``be_root``,
``fe``, ``fe_root``).  Run ``foundry targets list`` to verify.

The CLI
-------

``foundry generate``
^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   foundry generate --target NAME --config PATH [--out DIR] [--clean]

``--target / -t``
    Which registered target to use.  Required when more than one
    target is installed (the default with ``kiln-generator``); may
    be omitted when only one target is on the path.  Common values:
    ``be``, ``be_root``, ``fe``, ``fe_root``.
``--config / -c`` *(required)*
    Path to the ``.json`` or ``.jsonnet`` config file.
``--out / -o`` *(optional)*
    Output root directory.  Defaults to the target's own policy --
    ``be`` writes into the config's ``package_prefix`` value (e.g.
    ``_generated``).  Set ``package_prefix: ""`` in the config to
    write directly into the current directory.
``--clean``
    Run ``foundry clean`` before generating.  Useful when you remove a
    resource from the config -- without ``--clean`` the previously
    generated files for that resource stay on disk.

Re-running ``foundry generate`` is always safe: every generated file is
overwritten.  Never edit files under the output directory -- the next
run will discard your changes.

``foundry clean``
^^^^^^^^^^^^^^^^^

.. code-block:: text

   foundry clean --config PATH [--out DIR] [--target NAME]

Deletes the output directory.  Resolves ``--out`` the same way
``foundry generate`` does, so pointing the two commands at the same
config produces matching paths.  The current working directory is
never deleted.

Config format
-------------

be accepts ``.json`` and ``.jsonnet`` files.  Jsonnet is
recommended: imports, variables, and array concatenation make it much
more ergonomic for sharing common patterns across resources.

Minimal single-resource config
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: jsonnet

   {
     version: "1",
     module: "myapp",
     resources: [{
       model: "myapp.models.Article",
       operations: ["get", "list", "create", "update", "delete"],
     }],
   }

Full config with auth and multiple databases
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: jsonnet

   {
     version: "1",
     module: "blog",
     package_prefix: "_generated",

     auth: {
       type: "jwt",
       secret_env: "JWT_SECRET",
       verify_credentials_fn: "blog.auth.verify_credentials",
     },

     databases: [
       { key: "primary",   url_env: "DATABASE_URL",  default: true },
       { key: "analytics", url_env: "ANALYTICS_URL", echo: true },
     ],

     operations: ["get", "list", "create", "update", "delete"],

     resources: [
       {
         model: "blog.models.Article",
         require_auth: true,
         generate_tests: true,
       },
       {
         model: "blog.models.ReadStat",
         db_key: "analytics",
         operations: ["get", "list"],
       },
     ],
   }

Notes on inheritance:

* ``operations`` at the root is the default applied to every resource
  that does not set its own ``operations`` list.
* ``databases`` is configured once at the root; resources choose one
  via ``db_key`` (or fall back to the database with ``default: true``).
* ``auth`` is configured once at the root; each resource opts in via
  ``require_auth`` (defaults to ``True``).

Background tasks (pgqueuer)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

kiln-generated apps integrate with `pgqueuer
<https://github.com/janbjorge/pgqueuer>`_ for background work.  See
:doc:`pgqueuer` for the full runbook (schema setup, defining
tasks, the worker factory, transactional-outbox enqueue from
request handlers, and common pitfalls).

Operations
----------

Each entry in a resource's ``operations`` list is either:

* **A string**, the operation name -- invokes the operation with
  default options.

  .. code-block:: jsonnet

     operations: ["get", "list", "create", "update", "delete"]

* **An object**, carrying per-operation options:

  .. code-block:: jsonnet

     {
       name: "create",
       fields: [
         { name: "title", type: "str" },
         { name: "body",  type: "str" },
       ],
     }

  Extra keys (``fields``, ``max_items``, …) are parsed by the
  operation's ``Options`` Pydantic model, so validation errors surface
  during config load rather than at generation time.

* **An action**, invoking a custom Python function as a POST
  endpoint:

  .. code-block:: jsonnet

     {
       name: "publish",
       fn: "blog.actions.publish",
       params: [{ name: "at", type: "datetime" }],
     }

  foundry generates a ``POST /{id}/publish`` handler that calls
  ``blog.actions.publish``.

  Pass ``status_code: 202`` (or any integer) to override the
  response status.  The default is 204 for ``-> None`` functions
  and 200 otherwise.

Nested (related-model) fields
-----------------------------

Read ops (``get`` / ``list``) can dump a related SQLAlchemy model
inline by giving a field ``type: "nested"``:

.. code-block:: jsonnet

   {
     name: "get",
     fields: [
       { name: "id",    type: "uuid" },
       { name: "title", type: "str" },
       {
         name: "author",
         type: "nested",
         model: "blog.models.Author",
         fields: [
           { name: "id",   type: "uuid" },
           { name: "name", type: "str" },
         ],
       },
     ],
   }

be emits a scoped sub-schema (``ArticleResourceAuthorNested``) and
a sub-serializer (``to_article_resource_author_nested``), and attaches
a ``selectinload(Article.author)`` to the handler's ``select(...)`` so
the relationship is eagerly loaded -- lazy access in async SQLAlchemy
would otherwise raise ``MissingGreenlet``.

Collections use ``many: true``; the outer type becomes
``list[...Nested]`` and the serializer list-comprehends:

.. code-block:: jsonnet

   {
     name: "articles", type: "nested",
     model: "blog.models.Article",
     fields: [ { name: "id", type: "uuid" }, { name: "title", type: "str" } ],
     many: true,
   }

Override the loader strategy per field with ``load:
"selectin" | "joined" | "subquery"`` -- default is ``"selectin"``.
Strategies mix across nesting levels, producing chains like
``joinedload(Task.project).selectinload(Project.owner)`` on the
outer ``select(...)``.  See :ref:`nested-fields` in the reference
for all keys.

The ``be/fields.libsonnet`` helper shortens the common shape:

.. code-block:: jsonnet

   local fields = import "be/fields.libsonnet";

   fields: [
     fields.id(),
     { name: "title", type: "str" },
     fields.nested("author", "blog.models.Author", [
       fields.id(),
       { name: "name", type: "str" },
     ]),
   ]

Nested fields are read-only: ``create`` / ``update`` request bodies
must use scalar fields.

Built-in operations
-------------------

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Scope
     - Output
   * - ``scaffold``
     - project
     - ``db/*_session.py``, ``auth/dependencies.py``, ``auth/router.py``
       (each set is gated on the matching config block being present)
   * - ``get``
     - resource
     - GET /{pk} route handler + response schema
   * - ``list``
     - resource
     - GET / route handler + list/filter/sort/paginate schemas
   * - ``create``
     - resource
     - POST / route handler + request schema
   * - ``update``
     - resource
     - PATCH /{pk} route handler + request schema
   * - ``delete``
     - resource
     - DELETE /{pk} route handler
   * - ``action``
     - resource
     - POST /{pk}/{slug} or POST /{slug} handler for a custom action
   * - ``auth``
     - resource
     - Augments handlers with ``current_user`` dependency (runs only
       when ``config.auth`` is set and resource opts in)
   * - ``router``
     - app
     - App-level ``routes/__init__.py`` that includes every resource
       router
   * - ``project_router``
     - project
     - Project-level ``routes/__init__.py`` that mounts every app
       router (multi-app projects only)

Multi-app projects
------------------

Wrap each app's config in an ``apps`` entry:

.. code-block:: jsonnet

   // project.jsonnet
   {
     version: "1",
     auth: { ... },
     databases: [ ... ],
     apps: [
       { config: import "blog.jsonnet",      prefix: "/blog" },
       { config: import "inventory.jsonnet", prefix: "/inventory" },
     ],
   }

Top-level ``auth``, ``databases``, and ``operations`` are merged into
each app config during generation.  Individual apps can still override
these by defining their own blocks.

Jsonnet stdlib
--------------

be bundles a small Jsonnet stdlib that is importable from any config
file without a path prefix (the ``be`` prefix resolves to the stdlib
directory shipped inside the package).

See :doc:`reference` for the full stdlib list.  The most common:

* ``be/auth/jwt.libsonnet`` -- ``auth.jwt(...)`` preset for JWT.
* ``be/db/databases.libsonnet`` -- ``db.postgres(...)`` constructor.
* ``be/fields.libsonnet`` -- ``fields.id()``, ``fields.timestamps()``,
  and ``fields.nested(name, model, fields, many=false, load="selectin")``.
* ``be/resources/presets.libsonnet`` -- ``resource.action(...)`` and
  ``resource.files(...)`` for bundling action operations onto a
  resource.  See :ref:`file-uploads` for the file-upload flow.

.. _file-uploads:

File uploads
------------

be supports a presigned-URL upload flow on top of the existing
``action`` machinery -- no new operation type, just a SQLAlchemy mixin
plus four ready-made action functions in :mod:`ingot.files`.

Install the ``files`` extra to bring in the ``boto3`` runtime
dependency::

    pip install 'kiln-generator[files]'
    # or: uv add 'kiln-generator[files]'

Without the extra, importing :mod:`ingot.files` raises
``ModuleNotFoundError`` -- the gate is at the import boundary, not
deferred to first call, so failures surface immediately at app
startup rather than mid-request.

The flow:

1. Client ``POST /files/upload`` with ``{filename, content_type,
   size_bytes}``; server creates a ``pending`` row and returns
   ``{id, upload_url}``.
2. Client ``PUT``s the file bytes to ``upload_url`` (S3 directly --
   bytes never touch the app server).
3. Client ``POST /files/{id}/complete`` to flip the row out of
   pending state.  Subsequent ``POST /files/{id}/download`` calls
   return short-lived presigned GET URLs.
4. ``POST /files/{id}/delete-file`` cascades: deletes the S3
   object, then the row.  Returns 204 No Content.

The model
^^^^^^^^^

Subclass :class:`ingot.files.FileMixin` on a pgcraft-mapped model
and let a PK plugin own the ``id`` column:

.. code-block:: python

   # myapp/models.py
   from ingot.files import FileMixin
   from pgcraft.factory import PGCraftSimple
   from pgcraft.plugins.pk import UUIDV4PKPlugin

   from myapp.db import Base


   class File(Base, FileMixin):
       __tablename__ = "files"
       __table_args__ = {"schema": "public"}
       __factory__ = PGCraftSimple
       __plugins__ = [UUIDV4PKPlugin()]

The mixin contributes the storage columns -- ``s3_key``,
``content_type``, ``size_bytes``, ``original_filename``,
``created_at``, and ``uploaded_at`` (NULL until the upload is
confirmed) -- and the ``UUIDV4PKPlugin`` plugin contributes the
``id`` column.  Keeping ``id`` plugin-owned (rather than declared
on the mixin) is what makes the mixin compose with pgcraft without
the duplicate-column error a mixin-declared ``id`` would trigger.

Multi-table apps just declare additional models with their own
``__tablename__`` and the same ``FileMixin`` + PK-plugin shape:

.. code-block:: python

   class ProfileImage(Base, FileMixin):
       __tablename__ = "profile_images"
       __table_args__ = {"schema": "public"}
       __factory__ = PGCraftSimple
       __plugins__ = [UUIDV4PKPlugin()]

The config
^^^^^^^^^^

Point a resource at the bound model and call ``resource.files()``:

.. code-block:: jsonnet

   local resource = import "be/resources/presets.libsonnet";

   {
     model: "myapp.models.File",
     pk: "id",
     pk_type: "uuid",
     operations: resource.files(),
   }

Routes generated (relative to the resource prefix):

* ``GET  /{id}`` -- get (FileMixin columns by default)
* ``POST /upload`` -- request_upload (mints presigned PUT URL)
* ``POST /{id}/complete`` -- complete_upload (204 No Content)
* ``POST /{id}/download`` -- download (returns presigned GET URL)
* ``POST /{id}/delete-file`` -- delete_file (cascades S3 + row
  delete; 204 No Content)

The download endpoint is ``POST`` rather than ``GET`` because the
underlying ``action`` operation only supports POST today; the
response carries the GET URL the client follows.

The action functions in :mod:`ingot.files` use the resource's
mapped class via the introspector's supertype match -- object
actions take ``file: FileMixin`` (any concrete subclass matches
the instance), and ``request_upload`` takes
``model_cls: type[FileMixin]`` so the handler can plug in the
class for the ``INSERT``.  No per-resource glue module is needed.

Customizing the get fields:

.. code-block:: jsonnet

   resource.files(
     fields=[
       { name: "id", type: "uuid" },
       { name: "original_filename", type: "str" },
       { name: "content_type", type: "str" },
     ],
   )

Pass ``include_get=false`` to skip the get entirely (e.g. when you
want to attach your own ``get`` op with extra non-mixin fields):

.. code-block:: jsonnet

   operations: [
     { name: "get", fields: [...own fields including custom columns...] },
   ] + resource.files(include_get=false)

S3 configuration
^^^^^^^^^^^^^^^^

The action functions call :func:`ingot.files.default_storage`
which reads three env vars:

* ``KILN_S3_BUCKET`` -- bucket name (required).
* ``KILN_S3_REGION`` -- AWS region; optional, falls back to the
  boto3 default chain.
* ``KILN_S3_ENDPOINT_URL`` -- override for MinIO / localstack /
  other S3-compatible endpoints; optional.

For tests, monkey-patch ``ingot.files.default_storage`` to return
a mock :class:`ingot.files.S3Storage`.

.. _filtering:

Filtering, search, and saved views
----------------------------------

be ships an opt-in surface for table-style UIs: structured
filter specs, BE-powered value providers, a project-wide search
endpoint, and a serializer hook saved-view resources can use for
ref hydration.  The rest of this section is the user-facing
surface.

Structured filter blocks
^^^^^^^^^^^^^^^^^^^^^^^^

The ``filter`` modifier on a list op accepts a richer spec than
the bare-string field list.  Each entry can declare its
operators, value source, and any source-specific metadata:

.. code-block:: jsonnet

   list.searchable(
     fields=[
       { name: "id", type: "uuid" },
       { name: "sku", type: "str" },
       { name: "status", type: "str" },
       { name: "is_archived", type: "bool" },
     ],
     filter={
       fields: [
         { name: "status", operators: ["eq", "in"],
           values: "enum", enum: "myapp.models.OrderStatus" },
         { name: "is_archived", values: "bool" },
         { name: "sku", values: "free_text" },
         { name: "customer_id", values: "ref",
           ref_resource: "customer" },
         "name",  // shorthand: free_text + full operator vocab
       ],
     },
     order={ fields: ["sku", "name"], default: "sku" },
   )

This generates three things:

* The existing ``POST /search`` filter execution (unchanged).
* ``GET /_filters`` — discovery payload describing every
  filterable field, with ``enum`` choices inlined.  Sort metadata
  from the ``order`` modifier rides on the same response.
* ``GET /_filters/{field}`` — per-field discovery for lazy UIs
  that render one filter at a time.
* ``POST /_values/{field}`` — value provider per ``enum`` /
  ``free_text`` field.  ``bool`` and ``literal`` modes have no
  endpoint (the FE renders them natively).
* For ``ref`` fields, the discovery payload's ``endpoint``
  points at the *target* resource's resource-level
  ``_values``; no route is emitted on the source side.

Operator vocabulary: ``eq``, ``neq``, ``gt``, ``gte``, ``lt``,
``lte``, ``contains``, ``starts_with``, ``in``, ``is_null`` --
matched 1:1 with :data:`ingot.filters.FilterOp`.  Defaults are
derived from the ``values`` kind so most fields don't need an
explicit ``operators`` list.

Resource search
^^^^^^^^^^^^^^^

``searchable: true`` on a resource generates ``POST /_values``
returning items shaped by the resource's :ref:`link config
<links>`.  Powers ``ref`` filter inputs on other resources and
any FE "search this table" affordance.  ``search.fields``
overrides the default ILIKE target (``link.name``):

.. code-block:: jsonnet

   {
     model: "myapp.models.Customer",
     searchable: true,
     link: { kind: "id_name", name: "name" },
     search: { fields: ["name", "email"] },  // optional
     // ...
   }

Body: ``{q, cursor?, limit?}``.  Response:
``{results: [{type, id, name}, ...], next_cursor}``.  Auth is
required (the link builder receives the session).

.. _links:

Link schemas
^^^^^^^^^^^^

Resources that show up as cross-resource references — search
results, ``ref`` filter values, saved-view items — declare a
``link:`` block describing how they serialize.  The BE returns
structured fields, the FE assembles display strings:

.. code-block:: jsonnet

   // Shorthand: pulls fields straight off the model.
   link: { kind: "id_name", name: "title" }

   // Custom: any logic, returns the link schema instance.
   link: { kind: "id_name",
           builder: "myapp.labels.order_link" }

Each linked resource gets a per-resource ``{Model}Link``
Pydantic class generated into its schemas file (e.g.
``CustomerLink``, ``ProductLink``).  ``type`` is a
``Literal[<slug>]`` so the FE-side OpenAPI client narrows on
resource type:

* ``name`` -> ``{Model}Link{type, name}`` -- label-only.
* ``id`` -> ``{Model}Link{type, id}`` -- id-only.
* ``id_name`` -> ``{Model}Link{type, id, name}`` -- the default
  for most resources.

A custom builder is an ``async (instance, session) ->
LinkSchema`` function (matching the ``can`` guard signature so
session is available for permission-aware redaction).

The codegen emits ``{app_module}/links.py`` per app with two
maps keyed by slug: ``LINKS`` (link builder per resource) and
``REF_RESOLVERS`` (fetch-by-id-and-link per resource).  The
saved-view hydration helper consumes ``REF_RESOLVERS``.

.. _saved-views:

Saved views
^^^^^^^^^^^

Saved views are not a special opt-in.  The consumer subclasses
:class:`ingot.saved_views.SavedViewMixin` on their own
``DeclarativeBase`` and points a normal kiln resource at it,
using the ``resource.saved_views(...)`` jsonnet preset to wire
the standard CRUD ops:

.. code-block:: python

   # myapp/models.py
   from ingot.saved_views import SavedViewMixin
   from myapp.db import Base

   class SavedView(Base, SavedViewMixin):
       __tablename__ = "saved_views"

.. code-block:: jsonnet

   local resource = import "be/resources/presets.libsonnet";

   {
     model: "myapp.models.SavedView",
     pk: "id", pk_type: "str",
     require_auth: true,
     operations: resource.saved_views(
       serializer="myapp.serializers.dump_view_hydrated",
       owner_guard="myapp.guards.is_view_owner",
     ),
   }

The user's serializer wraps :func:`ingot.saved_views.hydrate_view`:

.. code-block:: python

   # myapp/serializers.py
   from _generated.myapp.links import REF_RESOLVERS
   from ingot.saved_views import hydrate_view

   async def dump_view_hydrated(view, session, db):
       return await hydrate_view(view, REF_RESOLVERS, db, session)

The mixin owns ``resource_type``, ``owner_id``, ``name``,
``payload`` (JSON), ``created_at``, ``updated_at``.  Per-user
scoping is the ``is_view_owner`` guard
(``async (resource, session) -> bool``).  Resource-type
filtering rides on the structured filter machinery.  Stored
``ref`` filter values keep raw ids; ``hydrate_view`` runs them
through the per-app ``REF_RESOLVERS`` at read time.

Custom serializers
^^^^^^^^^^^^^^^^^^

The ``serializer:`` hook on read ops (``get`` / ``list``) is
generic.  Set it to a dotted path to an
``async (obj, session, db) -> Any`` function and the generated
route calls it instead of the auto-generated
``to_<model>_resource`` / ``to_<model>_list_item``.
``response_model`` is dropped so the function may return any
JSON-serializable shape.  Useful for joined-row flattening,
computed fields that need DB access, or anything else the
auto-dump path can't express.

Testing the generated code
--------------------------

Setting ``generate_tests: true`` on a resource emits a pytest file
under ``_generated/.../tests/test_{name}.py``.  The file contains one
test per generated operation; run them with pytest as usual::

   uv run pytest _generated/

API versioning
--------------

be has no built-in ``--version`` flag.  To maintain multiple API
versions, run ``foundry generate`` against separate configs into separate
output trees and mount each at a different prefix:

.. code-block:: bash

   foundry generate --config v1.jsonnet --out _generated_v1/
   foundry generate --config v2.jsonnet --out _generated_v2/

.. code-block:: python

   from _generated_v1.myapp.routes import router as v1_router
   from _generated_v2.myapp.routes import router as v2_router

   app.include_router(v1_router, prefix="/v1")
   app.include_router(v2_router, prefix="/v2")

Other targets
-------------

``be_root`` -- backend bootstrap
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``be_root`` is a one-shot scaffolder that emits the boilerplate a
``be``-driven project needs once: ``main.py``, ``pyproject.toml``,
``justfile``, ``.gitignore``, ``.python-version``, the starter
``config/project.jsonnet``, and (when ``auth: true``) an
``auth.py`` skeleton.  Drive it with a ``bootstrap.jsonnet`` like:

.. code-block:: jsonnet

   {
     name: "myapp",
     module: "myapp",
     description: "FastAPI backend.",
     opentelemetry: false,
     auth: true,
     psycopg: true,
     pgcraft: false,
     pgqueuer: false,
     editable: false,
   }

Run::

   foundry generate --target be_root --config bootstrap.jsonnet --out .

Every emitted file is ``if_exists="skip"``, so re-running after
editing the bootstrap config is non-destructive.  ``--force``
clobbers everything; ``--force-paths a,b,c`` clobbers only the
listed paths.

``fe`` -- React / TypeScript codegen
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The ``fe`` target is a thin wrapper over
`@hey-api/openapi-ts <https://heyapi.dev/>`_: it translates a
kiln-side ``config/fe.jsonnet`` into ``openapi-ts.config.ts``, and
the openapi-ts CLI (``yarn openapi-ts``) then produces the actual
TypeScript SDK plus
`@tanstack/react-query <https://tanstack.com/query>`_ hooks.  The
config is small:

.. code-block:: jsonnet

   {
     openapi_spec: "../be/openapi.json",
     output_dir: "src/_generated",
     client: "@hey-api/client-fetch",
     react_query: true,
   }

Run::

   foundry generate --target fe --config config/fe.jsonnet --out .
   yarn openapi-ts

The ``fe_root``-emitted ``justfile`` chains those two steps under
``just generate``.

``fe_root`` -- frontend bootstrap
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``fe_root`` is the ``fe`` counterpart to ``be_root`` -- a one-shot
scaffolder that emits a yarn / Vite / TypeScript / React project
with React Query, openapi-ts, and (optionally) the
`@roddarjohn/glaze <https://github.com/roddarjohn/glaze>`_
component library wired up.  Drive it with:

.. code-block:: jsonnet

   {
     name: "myapp-fe",
     description: "React frontend.",
     glaze: true,
     editable: false,
     openapi_spec: "../be/openapi.json",
   }

Run::

   foundry generate --target fe_root --config bootstrap.jsonnet --out .

Output: ``package.json``, ``justfile``, ``tsconfig.json``,
``vite.config.ts``, ``index.html``, ``src/{main,App}.tsx``,
``src/index.css``, ``.gitignore``, ``.nvmrc``, and the starter
``config/fe.jsonnet``.  Same ``if_exists="skip"`` /
``--force`` / ``--force-paths`` semantics as ``be_root``.

Extending be
--------------

To add your own operations, swap renderers, or build an entirely new
target, see :doc:`extending`.  For the underlying architecture, see
:doc:`architecture`.
