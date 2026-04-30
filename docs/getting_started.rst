Getting started
===============

This guide walks through setting up a new project with the **be** target
from scratch.  By the end you will have a working FastAPI application
with generated routes, schemas, and (optionally) auth wired to
SQLAlchemy models you define yourself.

For the fastest path -- a fully scaffolded ``main.py`` /
``pyproject.toml`` / ``justfile`` / ``config/project.jsonnet``
skeleton -- skip ahead to :ref:`bootstrap-with-be-root`; the rest of
this page walks through the underlying pieces by hand.

For the React / TypeScript side, see :doc:`usage` -- the ``fe`` and
``fe_root`` targets are introduced there.

What be generates -- and doesn't
---------------------------------

The ``be`` target generates FastAPI code from a config file.  Specifically,
it produces:

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

be does **not** generate your SQLAlchemy models.  You write those
yourself and point the config at them by dotted import path.

Prerequisites
-------------

* Python 3.14+
* `uv <https://docs.astral.sh/uv/>`_ (recommended) or pip
* A PostgreSQL database, if you want to run the generated code

Install
-------

.. code-block:: bash

   pip install kiln-generator

   # or with uv
   uv add kiln-generator

   # or, to make ``foundry`` available globally without adding a
   # Python dep to your project:
   uv tool install kiln-generator

Verify the CLI is available::

   foundry --help
   foundry targets list

``kiln-generator`` ships four targets (``be``, ``be_root``, ``fe``,
``fe_root``).  When more than one is installed, every ``foundry``
command takes ``--target <name>`` to pick.

.. _bootstrap-with-be-root:

Fast path: bootstrap with ``be_root``
-------------------------------------

The ``be_root`` target is a one-shot scaffolder that emits the
boilerplate you'd otherwise type by hand: ``main.py``,
``pyproject.toml``, ``justfile``, ``.gitignore``, ``.python-version``,
and the starter ``config/project.jsonnet``.  Write a small
``bootstrap.jsonnet`` describing the project's identity and which
optional bits to enable:

.. code-block:: jsonnet

   {
     name: "myapp",
     module: "myapp",
     description: "FastAPI backend bootstrapped by be_root.",
     opentelemetry: false,
     auth: true,
     psycopg: true,
     pgcraft: false,
     pgqueuer: false,
     editable: false,
     rate_limit: false,
     comms: false,
   }

Setting ``rate_limit: true`` adds the ``kiln-generator[rate-limit]``
extra and stamps a ``rate_limit: rate_limit.slowapi('...')`` block
into ``config/project.jsonnet`` pointing at a placeholder bucket-model
dotted path you fill in once your model exists.

Setting ``comms: true`` stamps a ``comms: comms.platform({...})``
block and emits a starter ``comms.py`` skeleton with stub context
schemas, a stub :class:`~ingot.comms.Transport`, and a stub
:class:`~ingot.comms.PreferenceResolver`.  ``comms`` requires
``pgqueuer: true`` -- the bootstrap rejects the combination
otherwise so the broken state is caught at config-load time.  See
:doc:`comms` for the runtime surface.

Then run::

   foundry generate --target be_root --config bootstrap.jsonnet --out .

Every output is ``if_exists="skip"``, so re-running after editing the
bootstrap config is non-destructive.  Pass ``--force`` (or
``--force-paths a,b,c``) when you want a re-run to clobber.

After the bootstrap, jump to :ref:`step-3-generate` -- the Step 1
(SQLAlchemy models) and Step 2 (config) sections below describe what
the bootstrap already gave you.

Project layout
--------------

By default be writes all generated code into ``_generated/``
(controlled by the ``package_prefix`` config field).  A typical
single-app project looks like:

.. code-block:: text

   myproject/
   ├── config/
   │   └── project.jsonnet    # be config
   ├── myapp/
   │   └── models.py          # your hand-written SQLAlchemy models
   ├── main.py                # your FastAPI entry point
   └── _generated/            # written by be (never edit)
       ├── auth/              # get_current_user dependency
       ├── db/                # async session factories
       └── myapp/
           ├── routes/        # FastAPI routers
           ├── schemas/       # Pydantic request/response models
           └── serializers/   # model-to-schema helpers

Everything under ``_generated/`` is overwritten on every
``foundry generate --target be`` run.  Source-control the config file
and your models, not the generated output.

Step 1 -- Define your SQLAlchemy models
---------------------------------------

be generates routes and schemas *around* SQLAlchemy models you
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

Create ``config/project.jsonnet`` at your project root:

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

* ``model`` is the dotted import path to your SQLAlchemy class.  be
  does not require a specific base class -- any SQLAlchemy
  ``DeclarativeBase`` subclass works.
* ``operations`` lists the operations to run for this resource.  A
  string is shorthand for the operation with default options.  An
  object with ``name`` carries per-operation options (here, specifying
  exactly which fields the ``CreateRequest`` schema should expose).
* ``databases`` produces one async session factory per entry.  Set
  ``default: true`` on exactly one; resources omitting ``db_key`` use
  the default.

.. _step-3-generate:

Step 3 -- Generate
------------------

Run the CLI::

   foundry generate --target be --config config/project.jsonnet

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
(Alembic is a common choice).  be does not manage schema migrations.

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

You provide ``verify_credentials`` -- be generates the rest
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

``foundry generate --target be --config project.jsonnet`` produces the
per-app code plus a top-level ``_generated/routes/__init__.py`` that
mounts each app at its prefix.  Mount that in FastAPI:

.. code-block:: python

   from _generated.routes import router

   app = FastAPI()
   app.include_router(router, prefix="/v1")

See the :doc:`playground` for a runnable multi-app example with auth,
multiple databases, and custom actions.

Where to next
-------------

* :doc:`usage` -- day-to-day usage patterns, the full be config shape,
  and an introduction to the ``fe`` / ``fe_root`` frontend targets.
* :doc:`extending` -- add your own operations, renderers, or targets.
* :doc:`architecture` -- how the engine, scopes, operations, and
  renderers fit together.
* :doc:`reference` -- the exhaustive config reference.
