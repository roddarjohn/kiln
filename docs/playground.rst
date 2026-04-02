Playground
==========

The ``playground/`` directory in the kiln repository is a runnable
multi-app FastAPI project that demonstrates kiln's project mode.  It
generates a blog API and an inventory API from Jsonnet configs, mounts
them both under ``/v1/``, and serves them with uvicorn.

It is the fastest way to see kiln end-to-end without setting up your
own project.

Structure
---------

.. code-block:: text

   playground/
   ├── examples/
   │   ├── project.jsonnet   # top-level project config
   │   ├── blog.jsonnet      # blog app config
   │   └── inventory.jsonnet # inventory app config
   ├── blog/
   │   └── db/views/
   │       └── published_articles.py  # hand-written query function
   ├── generated/            # written by kiln generate (git-ignored)
   ├── main.py               # FastAPI entry point
   └── justfile              # convenience recipes

``generated/`` is the output directory.  Everything inside it is
overwritten on every ``kiln generate`` run and should not be edited
by hand.

The ``blog/`` directory contains hand-written code that coexists with
the generated ``generated/blog/`` package via Python
`namespace packages <https://peps.python.org/pep-0420/>`_ — no
``__init__.py`` in either directory, both parent directories on
``sys.path``.

Quick start
-----------

From the repo root, install all dependency groups::

    uv sync --all-groups

Then from ``playground/``::

    just rg    # reset generated/ and regenerate from examples/project.jsonnet
    just s     # start uvicorn with --reload

Or in one step::

    just f     # reset, generate, then serve

The server starts at ``http://localhost:8000``.  Interactive docs are
at ``http://localhost:8000/docs``.

Justfile recipes
----------------

All recipes are run from the ``playground/`` directory.

.. list-table::
   :header-rows: 1
   :widths: 20 10 70

   * - Recipe
     - Alias
     - Description
   * - ``just reset``
     - ``just r``
     - Delete ``generated/`` entirely.
   * - ``just generate``
     - ``just g``
     - Run ``kiln generate`` against ``examples/project.jsonnet``.
   * - ``just regen``
     - ``just rg``
     - Reset then generate (full clean rebuild).
   * - ``just serve``
     - ``just s``
     - Start uvicorn with hot-reload.
   * - ``just fresh``
     - ``just f``
     - Reset, generate, then serve.

Project config
--------------

``examples/project.jsonnet`` is a **project-level** config — it declares
shared auth and databases, then lists each app with its URL prefix:

.. code-block:: jsonnet

   local auth = import "kiln/auth/jwt.libsonnet";
   local db   = import "kiln/db/databases.libsonnet";

   {
     auth: auth.jwt({ secret_env: "JWT_SECRET" }),

     databases: [
       db.postgres("primary", { default: true }),
       db.postgres("analytics", { url_env: "ANALYTICS_DATABASE_URL" }),
     ],

     apps: [
       { config: import "blog.jsonnet",      prefix: "/blog"      },
       { config: import "inventory.jsonnet", prefix: "/inventory" },
     ],
   }

Each app config (``blog.jsonnet``, ``inventory.jsonnet``) is a
self-contained kiln config with its own ``module``, ``models``, and
``views``.  Kiln merges the project-level ``auth`` and ``databases``
into each app config before generating, so app configs do not need to
redeclare them.

Apps
----

**blog** (``module: "blog"``)
    Demonstrates plain (non-parameterised) views and mixed CRUD
    configs.  The ``Author`` model is read-only; ``Article`` and
    ``Tag`` have full CRUD.  The ``published_articles`` view uses a
    ``query_fn`` pointing to a hand-written SQLAlchemy ``select()``
    in ``blog/db/views/published_articles.py`` — kiln imports and
    calls it from the generated route, so the query logic lives
    outside the generated directory.

**inventory** (``module: "inventory"``)
    Demonstrates an append-only pgcraft type (``PGCraftAppendOnly``),
    a cross-model foreign key, and a parameterised set-returning
    function view.  ``StockMovement`` records are write-only (no
    update or delete); the ``stock_levels_by_date`` view accepts
    date-range parameters and is served as a ``GET`` route.

Hand-written query functions
----------------------------

For non-parameterised views, kiln generates a route that calls a
``query_fn`` you provide.  The function must accept no arguments and
return a SQLAlchemy ``select()`` expression.

In the playground, ``blog/db/views/published_articles.py`` provides
``get_query`` for the ``published_articles`` view:

.. code-block:: python

   from sqlalchemy import select
   from blog.models.article import Article
   from blog.models.author import Author

   def get_query():
       return (
           select(
               Article.id,
               Article.title,
               Article.slug,
               Author.name.label("author_name"),
               Article.created_at.label("published_at"),
           )
           .join(Author, Article.author_id == Author.id)
           .where(Article.published.is_(True))
           .order_by(Article.created_at.desc())
       )

Point kiln at it via the config:

.. code-block:: jsonnet

   query_fn: "blog.db.views.published_articles.get_query"

Kiln imports the dotted path at request time and calls the function to
build the query.
