Usage
=====

.. contents:: On this page
   :local:
   :depth: 2

This page covers day-to-day usage of the ``kiln`` CLI.  For a
walkthrough of a brand-new project, see :doc:`getting_started`.  For
the complete config schema, see :doc:`reference`.

Install
-------

.. code-block:: bash

   pip install kiln-generator  # or: uv add kiln-generator

The CLI
-------

``kiln generate``
^^^^^^^^^^^^^^^^^

.. code-block:: text

   kiln generate --config PATH [--out DIR] [--clean]

``--config / -c`` *(required)*
    Path to the ``.json`` or ``.jsonnet`` config file.
``--out / -o`` *(optional)*
    Output root directory.  Defaults to the config's
    ``package_prefix`` value (e.g. ``_generated``).  Set
    ``package_prefix: ""`` in the config to write directly into the
    current directory.
``--clean``
    Delete ``--out`` before generating.  Useful when you remove a
    resource from the config -- without ``--clean`` the previously
    generated files for that resource stay on disk.

Re-running ``kiln generate`` is always safe: every generated file is
overwritten.  Never edit files under the output directory -- the next
run will discard your changes.

Config format
-------------

kiln accepts ``.json`` and ``.jsonnet`` files.  Jsonnet is
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

  kiln generates a ``POST /{id}/publish`` handler that calls
  ``blog.actions.publish``.

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

kiln bundles a small Jsonnet stdlib that is importable from any config
file without a path prefix (the ``kiln`` prefix resolves to the stdlib
directory shipped inside the package).

See :doc:`reference` for the full stdlib list.  The most common:

* ``kiln/auth/jwt.libsonnet`` -- ``auth.jwt(...)`` preset for JWT.
* ``kiln/db/databases.libsonnet`` -- ``db.postgres(...)`` constructor.

Testing the generated code
--------------------------

Setting ``generate_tests: true`` on a resource emits a pytest file
under ``_generated/.../tests/test_{name}.py``.  The file contains one
test per generated operation; run them with pytest as usual::

   uv run pytest _generated/

API versioning
--------------

kiln has no built-in ``--version`` flag.  To maintain multiple API
versions, run ``kiln generate`` against separate configs into separate
output trees and mount each at a different prefix:

.. code-block:: bash

   kiln generate --config v1.jsonnet --out _generated_v1/
   kiln generate --config v2.jsonnet --out _generated_v2/

.. code-block:: python

   from _generated_v1.myapp.routes import router as v1_router
   from _generated_v2.myapp.routes import router as v2_router

   app.include_router(v1_router, prefix="/v1")
   app.include_router(v2_router, prefix="/v2")

Extending kiln
--------------

To add your own operations, swap renderers, or build an entirely new
target, see :doc:`extending`.  For the underlying architecture, see
:doc:`architecture`.
