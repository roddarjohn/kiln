Getting started
===============

This guide walks through setting up a new project with kiln from scratch.
By the end you will have a working FastAPI application with generated
routes, schemas, and (optionally) auth wired to SQLAlchemy models you
define yourself.

What foundry generates -- and doesn't
-------------------------------------

foundry generates FastAPI code from a config file.  Specifically, it
produces:

* **Routes** -- one FastAPI router per resource, with handlers for the
  CRUD operations (and custom actions) you enable.
* **Pydantic schemas** -- request and response models for every route.
* **Serializers** -- model-to-schema helpers used by the generated
  handlers.
* **An app router** -- aggregates all resource routers into one
  ``APIRouter`` your FastAPI app can mount.
* **A project router** *(multi-app projects only)* -- mounts every
  app router under its configured prefix.
* **Scaffolding** -- database session factories (one per configured
  database) and, when auth is enabled, a ``get_current_user``
  dependency and optional login router.

kiln does **not** generate your SQLAlchemy models.  You write those
yourself and point the config at them by dotted import path.

Prerequisites
-------------

* Python 3.12+
* `uv <https://docs.astral.sh/uv/>`_ (recommended) or pip
* A PostgreSQL database, if you want to run the generated code

Install
-------

.. code-block:: bash

   pip install kiln-generator

   # or with uv
   uv add kiln-generator

Verify the CLI is available::

   foundry --help

kiln registers itself as a target for the generic ``foundry`` CLI
shipped in the same package, so ``foundry`` is the command you run.

Project layout
--------------

By default kiln writes all generated code into ``_generated/``
(controlled by the ``package_prefix`` config field).  A typical
single-app project looks like:

.. code-block:: text

   myproject/
   ├── app.jsonnet            # kiln config
   ├── myapp/
   │   └── models.py          # your hand-written SQLAlchemy models
   ├── main.py                # your FastAPI entry point
   └── _generated/            # written by kiln (never edit)
       ├── auth/              # get_current_user dependency
       ├── db/                # async session factories
       └── myapp/
           ├── routes/        # FastAPI routers
           ├── schemas/       # Pydantic request/response models
           └── serializers/   # model-to-schema helpers

Everything under ``_generated/`` is overwritten on every ``kiln
generate`` run.  Source-control the config file and your models, not
the generated output.

Step 1 -- Define your SQLAlchemy models
---------------------------------------

foundry generates routes and schemas *around* SQLAlchemy models you
define.  A minimal ``myapp/models.py``:

.. code-block:: python

   import uuid
   from datetime import datetime

   from sqlalchemy import DateTime, String, func
   from sqlalchemy.dialects.postgresql import UUID
   from sqlalchemy.orm import (
       DeclarativeBase, Mapped, mapped_column,
   )


   class Base(DeclarativeBase):
       pass


   class Article(Base):
       __tablename__ = "articles"

       id: Mapped[uuid.UUID] = mapped_column(
           UUID(as_uuid=True),
           primary_key=True,
           default=uuid.uuid4,
       )
       title: Mapped[str] = mapped_column(String)
       body: Mapped[str] = mapped_column(String)
       created_at: Mapped[datetime] = mapped_column(
           DateTime(timezone=True),
           server_default=func.now(),
       )

Step 2 -- Write a config
------------------------

Create ``app.jsonnet`` at your project root:

.. code-block:: jsonnet

   {
     version: "1",
     module: "myapp",
     package_prefix: "_generated",

     databases: [
       { key: "primary", url_env: "DATABASE_URL", default: true },
     ],

     resources: [{
       model: "myapp.models.Article",
       pk: "id",
       pk_type: "uuid",
       route_prefix: "/articles",
       require_auth: false,
       operations: [
         "get", "list", "create", "update", "delete",
         {
           name: "create",
           fields: [
             { name: "title", type: "str" },
             { name: "body",  type: "str" },
           ],
         },
       ],
     }],
   }

Key points:

* ``model`` is the dotted import path to your SQLAlchemy class.  kiln
  does not require a specific base class -- any SQLAlchemy
  ``DeclarativeBase`` subclass works.
* ``operations`` lists the operations to run for this resource.  A
  string is shorthand for the operation with default options.  An
  object with ``name`` carries per-operation options (here, specifying
  exactly which fields the ``CreateRequest`` schema should expose).
* ``databases`` produces one async session factory per entry.  Set
  ``default: true`` on exactly one; resources omitting ``db_key`` use
  the default.

Step 3 -- Generate
------------------

Run the CLI::

   foundry generate --config app.jsonnet

Output lands in ``_generated/``:

.. code-block:: text

   _generated/
   ├── db/
   │   ├── __init__.py
   │   └── primary_session.py
   └── myapp/
       ├── __init__.py
       ├── routes/
       │   ├── __init__.py          # app router
       │   └── article.py
       ├── schemas/
       │   └── article.py
       └── serializers/
           └── article.py

``--out`` overrides the output root; ``--clean`` runs ``foundry clean``
first to remove any stale files.

Step 4 -- Mount the router
--------------------------

Wire the generated router into your FastAPI app:

.. code-block:: python

   from fastapi import FastAPI

   from _generated.myapp.routes import router

   app = FastAPI()
   app.include_router(router, prefix="/v1")

Step 5 -- Environment and database
----------------------------------

The generated session factory reads the URL from an environment
variable (``url_env`` on the database config -- default
``DATABASE_URL``)::

   export DATABASE_URL="postgresql+asyncpg://user:pw@localhost/mydb"

Create the database tables with whatever migration tool you use
(Alembic is a common choice).  kiln does not manage schema migrations.

Step 6 -- Run the server
------------------------

::

   uvicorn main:app --reload

Interactive API docs land at ``http://localhost:8000/docs``.

Adding authentication
---------------------

Add an ``auth`` block to the config to turn on JWT authentication:

.. code-block:: jsonnet

   {
     ...,
     auth: {
       type: "jwt",
       secret_env: "JWT_SECRET",
       algorithm: "HS256",
       verify_credentials_fn: "myapp.auth.verify_credentials",
     },
     resources: [{
       model: "myapp.models.Article",
       require_auth: true,  // applies to all handlers on this resource
       ...
     }],
   }

You provide ``verify_credentials`` -- foundry generates the rest
(``auth/dependencies.py`` with ``get_current_user``, and
``auth/router.py`` with a ``/auth/token`` login endpoint).

Auth is implemented as a cross-cutting ``@operation`` with a
``when`` hook -- it runs whenever both ``config.auth`` is set and
``resource.require_auth`` is true.  No extra wiring is required on
your end.

Multi-app projects
------------------

For projects that bundle multiple apps (a blog API and an inventory
API, say), wrap each app's config in an ``apps`` list:

.. code-block:: jsonnet

   // project.jsonnet
   {
     version: "1",
     package_prefix: "_generated",
     auth: { type: "jwt", secret_env: "JWT_SECRET", ... },
     databases: [{ key: "primary", default: true }],

     apps: [
       { config: import "blog.jsonnet",      prefix: "/blog" },
       { config: import "inventory.jsonnet", prefix: "/inventory" },
     ],
   }

``foundry generate --config project.jsonnet`` produces the per-app code
plus a top-level ``_generated/routes/__init__.py`` that mounts each
app at its prefix.  Mount that in FastAPI:

.. code-block:: python

   from _generated.routes import router

   app = FastAPI()
   app.include_router(router, prefix="/v1")

See the :doc:`playground` for a runnable multi-app example with auth,
multiple databases, and custom actions.

Where to next
-------------

* :doc:`usage` -- day-to-day usage patterns and the full config shape.
* :doc:`extending` -- add your own operations, renderers, or
  generators.
* :doc:`architecture` -- how the engine, scopes, operations, and
  renderers fit together.
* :doc:`reference` -- the exhaustive config reference.
