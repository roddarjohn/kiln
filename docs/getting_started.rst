Getting started
===============

This guide walks through setting up a new project with kiln from scratch.
By the end you will have a working FastAPI application with generated
models, routes, and auth wired together.

Prerequisites
-------------

* Python 3.12+
* A PostgreSQL database
* `uv <https://docs.astral.sh/uv/>`_ (recommended) or pip

Install
-------

.. code-block:: bash

   pip install kiln-generator

   # or with uv
   uv add kiln-generator

Verify the CLI is available::

   kiln --help

Project layout
--------------

Kiln generates code into an output directory of your choice.  A typical
single-app project looks like this after generation:

.. code-block:: text

   myproject/
   ├── app.jsonnet          # kiln config
   ├── src/
   │   ├── main.py          # your FastAPI entry point
   │   ├── auth/            # generated — JWT dependency
   │   ├── db/              # generated — base + session
   │   └── myapp/
   │       ├── models/      # generated — pgcraft model classes
   │       ├── routes/      # generated — FastAPI routers
   │       └── schemas/     # generated — Pydantic request/response models

Everything under ``src/auth/``, ``src/db/``, and ``src/myapp/`` is
written by kiln and overwritten on every ``kiln generate`` run.  Do not
edit those files by hand.

Step 1 — Write a config
-----------------------

Create ``app.jsonnet`` in your project root.  Jsonnet is recommended
over plain JSON because it supports imports, variables, and comments.

.. code-block:: jsonnet

   local auth  = import 'kiln/auth/jwt.libsonnet';
   local db    = import 'kiln/db/databases.libsonnet';
   local field = import 'kiln/models/fields.libsonnet';
   local crud  = import 'kiln/crud/presets.libsonnet';

   {
     version: "1",
     module: "myapp",

     auth: auth.jwt({ secret_env: "JWT_SECRET" }),

     databases: [
       db.postgres("primary", { default: true }),
     ],

     models: [
       {
         name: "Post",
         table: "posts",
         schema: "public",
         pgcraft_type: "simple",
         fields: [
           field.uuid("id", primary_key=true),
           field.str("title"),
           field.str("body"),
           field.bool("published"),
           field.datetime("created_at", auto_now_add=true),
           field.datetime("updated_at", auto_now=true),
         ],
         crud: crud.full({ require_auth: ["create", "update", "delete"] }),
       },
     ],
   }

The stdlib imports (``kiln/auth/…``, ``kiln/models/…``, etc.) are
bundled with kiln and are always available without any extra setup.

Step 2 — Generate
-----------------

Run kiln against the config, writing output into ``src/``::

   kiln generate --config app.jsonnet --out src/

Kiln creates:

* ``src/auth/dependencies.py`` — ``get_current_user`` FastAPI dependency
* ``src/db/base.py`` — ``PGCraftBase`` subclass shared by all models
* ``src/db/primary_session.py`` — async SQLAlchemy session factory
* ``src/myapp/models/post.py`` — pgcraft declarative model class
* ``src/myapp/schemas/post.py`` — Pydantic ``PostCreate``, ``PostUpdate``, ``PostResponse``
* ``src/myapp/routes/post.py`` — FastAPI CRUD router
* ``src/myapp/routes/__init__.py`` — aggregated router

Re-running ``kiln generate`` is always safe — all files are overwritten.

Step 3 — Mount the router
-------------------------

Create ``src/main.py`` (or add to your existing entry point):

.. code-block:: python

   import sys
   from pathlib import Path

   # Put src/ on the path so generated packages are importable.
   sys.path.insert(0, str(Path(__file__).parent))

   from fastapi import FastAPI
   from myapp.routes import router

   app = FastAPI()
   app.include_router(router, prefix="/v1")

Step 4 — Set environment variables
-----------------------------------

Kiln-generated auth and session code reads from environment variables::

   export DATABASE_URL="postgresql+asyncpg://user:password@localhost/mydb"
   export JWT_SECRET="your-secret-key"

The variable names come from the config (``url_env`` and ``secret_env``).
Defaults are ``DATABASE_URL`` and ``JWT_SECRET``.

Step 5 — Run migrations and serve
----------------------------------

Kiln generates the SQLAlchemy model classes; `pgcraft
<https://github.com/roddarjohn/pgcraft>`_ then drives Alembic to create
the actual database tables.  Follow the pgcraft docs to run migrations,
then start the server::

   uvicorn main:app --reload --app-dir src/

Interactive API docs will be available at ``http://localhost:8000/docs``.

Multi-app projects
------------------

For projects with multiple apps (e.g. a blog and an inventory service),
use a **project-level config** that imports each app config and assigns
a URL prefix.

Create one config file per app (``blog.jsonnet``, ``inventory.jsonnet``),
then a top-level ``project.jsonnet``:

.. code-block:: jsonnet

   local auth = import 'kiln/auth/jwt.libsonnet';
   local db   = import 'kiln/db/databases.libsonnet';

   {
     auth: auth.jwt({ secret_env: "JWT_SECRET" }),

     databases: [
       db.postgres("primary", { default: true }),
     ],

     apps: [
       { config: import "blog.jsonnet",      prefix: "/blog"      },
       { config: import "inventory.jsonnet", prefix: "/inventory" },
     ],
   }

Run once::

   kiln generate --config project.jsonnet --out src/

Kiln generates all app code plus a root ``src/routes/__init__.py`` that
mounts each app router at its prefix.  Mount it in your FastAPI app:

.. code-block:: python

   from routes import router

   app = FastAPI()
   app.include_router(router, prefix="/v1")

See the :doc:`playground` for a working multi-app example with auth,
multiple databases, views, and a hand-written query function.

Config reference
----------------

Fields
^^^^^^

Defined with ``field.<type>(name, **opts)`` from
``kiln/models/fields.libsonnet``:

.. list-table::
   :header-rows: 1
   :widths: 15 55 30

   * - Helper
     - Description
     - Key options
   * - ``field.uuid``
     - UUID column
     - ``primary_key``, ``foreign_key``, ``nullable``
   * - ``field.str``
     - VARCHAR / TEXT column
     - ``unique``, ``nullable``, ``index``
   * - ``field.email``
     - Email string (validated in schema)
     - ``unique``, ``nullable``
   * - ``field.int``
     - Integer column
     - ``primary_key``, ``foreign_key``, ``nullable``
   * - ``field.float``
     - Float column
     - ``nullable``
   * - ``field.bool``
     - Boolean column
     - ``nullable``
   * - ``field.datetime``
     - Timestamp with timezone
     - ``auto_now_add``, ``auto_now``, ``nullable``
   * - ``field.date``
     - Date column
     - ``nullable``
   * - ``field.json``
     - JSONB column
     - ``nullable``

CRUD presets
^^^^^^^^^^^^

Defined with ``crud.<preset>(opts)`` from ``kiln/crud/presets.libsonnet``:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Preset
     - Operations enabled
   * - ``crud.full``
     - create, read, update, delete, list
   * - ``crud.read_only``
     - read, list
   * - ``crud.no_list``
     - create, read, update, delete
   * - ``crud.write_only``
     - create, update, delete

Pass ``require_auth: ["create", "update", "delete"]`` in ``opts`` to
require a valid JWT for specific operations.

pgcraft model types
^^^^^^^^^^^^^^^^^^^

Set via ``pgcraft_type`` on a model:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Type
     - Description
   * - ``simple``
     - Standard dimension table (default)
   * - ``append_only``
     - Immutable ledger — no update or delete at the DB level
   * - ``ledger``
     - Double-entry ledger with balance tracking
   * - ``eav``
     - Entity–attribute–value table

See the `pgcraft docs <https://github.com/roddarjohn/pgcraft>`_ for
details on what each type generates in PostgreSQL.
