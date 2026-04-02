Usage
=====

Install
-------

.. code-block:: bash

   pip install kiln            # or: uv add kiln

Kiln generates FastAPI routes and `pgcraft <https://github.com/roddarjohn/pgcraft>`_
model definitions from a declarative config file.  pgcraft then drives
Alembic migrations and PostgreSQL DDL (tables, views, triggers, functions).

Quick start
-----------

1. **Scaffold** the one-time boilerplate into your app directory::

      kiln init --out ./src/app

   This creates:

   * ``auth/dependencies.py`` — JWT ``get_current_user`` FastAPI dependency
   * ``db/base.py`` — ``PGCraftBase`` subclass shared by all models
   * ``db/session.py`` — async SQLAlchemy session factory

2. **Write a config file** (``myapp.jsonnet`` or ``myapp.json``):

   .. code-block:: jsonnet

      local auth  = import 'kiln/auth/jwt.libsonnet';
      local field = import 'kiln/models/fields.libsonnet';
      local crud  = import 'kiln/crud/presets.libsonnet';

      {
        version: "1",
        module: "app",
        auth: auth.jwt({ secret_env: "JWT_SECRET" }),

        models: [{
          name: "Post",
          table: "posts",
          schema: "public",
          pgcraft_type: "simple",
          fields: [
            field.uuid("id", primary_key=true),
            field.str("title"),
            field.uuid("author_id", foreign_key="users.id"),
          ],
          crud: crud.full({ require_auth: ["create", "update", "delete"] }),
        }],
      }

3. **Generate** the model and route files::

      kiln generate --config myapp.jsonnet --out ./src/app

   This creates or updates:

   * ``db/models/post.py`` — pgcraft declarative model
   * ``api/routes/post.py`` — FastAPI CRUD router with Pydantic schemas
   * ``api/__init__.py`` — aggregating router (``from app.api import router``)

4. **Mount** the generated router in your FastAPI app:

   .. code-block:: python

      from fastapi import FastAPI
      from app.api import router

      app = FastAPI()
      app.include_router(router)

Re-running ``kiln generate`` is safe: model and route files are always
refreshed; pgcraft stub files (``db/views/``) are never overwritten so
hand-written SQL is preserved.

Config format
-------------

Kiln supports ``.json`` and ``.jsonnet`` config files.  Jsonnet is
recommended because it allows imports, variables, and composition.

The bundled stdlib (importable as ``kiln/...``) provides helpers:

* ``kiln/auth/jwt.libsonnet`` — JWT auth preset
* ``kiln/models/fields.libsonnet`` — field constructors (``uuid``, ``str``, ``email``, …)
* ``kiln/crud/presets.libsonnet`` — CRUD presets (``full``, ``read_only``, …)

The full config schema is documented in
:mod:`kiln.config.schema`.

Views and database functions
----------------------------

Kiln can also generate FastAPI routes that query **views** or
**set-returning functions** managed by pgcraft.

Add a ``views`` entry to the config:

.. code-block:: jsonnet

   views: [
     // Non-parameterised view  →  PGCraftViewMixin stub + SELECT route
     {
       name: "active_users",
       model: "User",
       schema: "public",
       returns: [
         { name: "id",    type: "uuid" },
         { name: "email", type: "str"  },
       ],
     },

     // Parameterised function  →  PGCraftFunctionMixin stub + func route
     {
       name: "summarize_posts_by_user",
       model: "Post",
       description: "Count posts per user in a date range.",
       schema: "public",
       http_method: "GET",
       require_auth: true,
       parameters: [
         { name: "start_date", type: "date" },
         { name: "end_date",   type: "date" },
       ],
       returns: [
         { name: "user_id",    type: "uuid" },
         { name: "post_count", type: "int"  },
       ],
     },
   ],

Kiln generates:

* ``db/views/<name>.py`` (``overwrite=False``) — fill in the pgcraft
  ``__query__`` or ``register_function`` definition, then run pgcraft
  migrations.
* ``api/views/<name>.py`` — FastAPI route that queries the named database
  object.  No SQL appears in the route file:

  * Views: ``select(view_table)``
  * Functions: ``func.<schema>.<name>(params).table_valued(cols)``

Extending kiln
--------------

Custom generators can be installed as Python packages and registered
via the ``kiln.generators`` entry-point group:

.. code-block:: toml

   [project.entry-points."kiln.generators"]
   typescript = "my_package.generators:TypeScriptClientGenerator"

The class must implement the
:class:`~kiln.generators.base.Generator` protocol and accept no
constructor arguments.  It is instantiated automatically by
:meth:`~kiln.generators.registry.GeneratorRegistry.discover_entry_points`
and included in every ``kiln generate`` run.

See :mod:`kiln.generators.base` for the full protocol definition.

API versioning
--------------

Kiln has no built-in ``--version`` flag.  To maintain multiple API
versions, run ``kiln generate`` into separate output directories and
mount each router at a different prefix:

.. code-block:: bash

   kiln generate --config v1.jsonnet --out ./src/app_v1
   kiln generate --config v2.jsonnet --out ./src/app_v2

.. code-block:: python

   from app_v1.api import router as v1_router
   from app_v2.api import router as v2_router

   app.include_router(v1_router, prefix="/v1")
   app.include_router(v2_router, prefix="/v2")
