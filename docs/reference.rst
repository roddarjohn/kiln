Reference
=========

.. contents:: On this page
   :local:
   :depth: 2

Config schema
-------------

All config files are parsed into a :class:`~kiln.config.schema.KilnConfig`
object.  The classes below correspond directly to the keys in your
``.jsonnet`` / ``.json`` config file.

.. autoclass:: kiln.config.schema.KilnConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.AppRef
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.ModelConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.FieldConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.CrudConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.ViewModel
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.ViewParam
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.ViewColumn
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.DatabaseConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.AuthConfig
   :members:
   :undoc-members:

Field types
^^^^^^^^^^^

The ``type`` field on :class:`~kiln.config.schema.FieldConfig` and
:class:`~kiln.config.schema.ViewColumn` accepts the following values:

.. list-table::
   :header-rows: 1
   :widths: 15 30 30 25

   * - Type
     - SQLAlchemy column
     - Pydantic / Python type
     - PostgreSQL SQL type
   * - ``uuid``
     - ``pg.UUID(as_uuid=True)``
     - ``uuid.UUID``
     - ``UUID``
   * - ``str``
     - ``String``
     - ``str``
     - ``TEXT``
   * - ``email``
     - ``String``
     - ``str``
     - ``TEXT``
   * - ``int``
     - ``Integer``
     - ``int``
     - ``INTEGER``
   * - ``float``
     - ``Float``
     - ``float``
     - ``DOUBLE PRECISION``
   * - ``bool``
     - ``Boolean``
     - ``bool``
     - ``BOOLEAN``
   * - ``datetime``
     - ``pg.TIMESTAMP(timezone=True)``
     - ``datetime``
     - ``TIMESTAMPTZ``
   * - ``date``
     - ``pg.DATE``
     - ``date``
     - ``DATE``
   * - ``json``
     - ``pg.JSONB``
     - ``dict[str, Any]``
     - ``JSONB``

Built-in generators
-------------------

Kiln ships four app-level generators and two structural generators.
All are registered automatically when you call
:meth:`~kiln.generators.registry.GeneratorRegistry.default`.

App-level generators
^^^^^^^^^^^^^^^^^^^^

These run once per app (or once in app mode) for every ``kiln generate``
invocation.

.. list-table::
   :header-rows: 1
   :widths: 30 25 45

   * - Generator
     - Runs when
     - Output files
   * - :class:`~kiln.generators.fastapi.models.PGCraftModelGenerator`
     - ``config.models`` is non-empty
     - ``{module}/models/{name}.py`` — one pgcraft declarative class per model
   * - :class:`~kiln.generators.fastapi.crud.CRUDGenerator`
     - any model has ``crud`` set
     - ``{module}/routes/{name}.py`` — CRUD router; ``{module}/schemas/{name}.py`` — Pydantic schemas
   * - :class:`~kiln.generators.fastapi.views.ViewGenerator`
     - ``config.views`` is non-empty
     - ``{module}/routes/{name}.py`` — one FastAPI route per view
   * - :class:`~kiln.generators.fastapi.router.RouterGenerator`
     - models or views are present
     - ``{module}/routes/__init__.py`` — aggregated router

Structural generators
^^^^^^^^^^^^^^^^^^^^^

These run outside the per-generator loop and produce shared infrastructure.

**ScaffoldGenerator**

Runs when auth or databases are configured (in app mode), or once at
the start of every project-mode build.  Produces:

* ``db/base.py`` — ``PGCraftBase`` subclass (``Base``) shared by all models
* ``db/{key}_session.py`` — async SQLAlchemy engine + ``AsyncSession`` factory per database
* ``auth/dependencies.py`` — ``get_current_user`` FastAPI dependency (JWT or custom)

**ProjectRouterGenerator**

Runs only in project mode (``config.apps`` is non-empty).  Produces:

* ``routes/__init__.py`` — root ``APIRouter`` that mounts every app router
  at its configured prefix

Generator details
^^^^^^^^^^^^^^^^^

.. autoclass:: kiln.generators.fastapi.models.PGCraftModelGenerator
   :members: name, can_generate, generate

.. autoclass:: kiln.generators.fastapi.crud.CRUDGenerator
   :members: name, can_generate, generate

.. autoclass:: kiln.generators.fastapi.views.ViewGenerator
   :members: name, can_generate, generate

.. autoclass:: kiln.generators.fastapi.router.RouterGenerator
   :members: name, can_generate, generate

.. autoclass:: kiln.generators.init.scaffold.ScaffoldGenerator
   :members: name, can_generate, generate

.. autoclass:: kiln.generators.fastapi.project_router.ProjectRouterGenerator
   :members: name, can_generate, generate

Generator registry
------------------

.. autoclass:: kiln.generators.registry.GeneratorRegistry
   :members:

Generator protocol
------------------

.. autoclass:: kiln.generators.base.Generator
   :members:

.. autoclass:: kiln.generators.base.GeneratedFile
   :members:

Generated file layout
---------------------

The table below summarises every file kiln can generate and which
generator produces it.  All paths are relative to the ``--out``
directory.

.. list-table::
   :header-rows: 1
   :widths: 45 30 25

   * - Path
     - Generator
     - Overwrite
   * - ``db/base.py``
     - ScaffoldGenerator
     - Yes
   * - ``db/{key}_session.py``
     - ScaffoldGenerator
     - Yes
   * - ``auth/dependencies.py``
     - ScaffoldGenerator
     - Yes
   * - ``{module}/models/__init__.py``
     - PGCraftModelGenerator
     - Yes
   * - ``{module}/models/{name}.py``
     - PGCraftModelGenerator
     - Yes
   * - ``{module}/schemas/{name}.py``
     - CRUDGenerator
     - Yes
   * - ``{module}/routes/{name}.py`` (CRUD)
     - CRUDGenerator
     - Yes
   * - ``{module}/routes/{name}.py`` (view)
     - ViewGenerator
     - Yes
   * - ``{module}/routes/__init__.py``
     - RouterGenerator
     - Yes
   * - ``routes/__init__.py``
     - ProjectRouterGenerator
     - Yes

All files use ``overwrite=True`` — re-running ``kiln generate`` always
produces a clean, up-to-date output directory.  Source-control the
config files, not the generated output.

Stdlib reference
----------------

The following ``.libsonnet`` files are bundled with kiln and importable
from any config file without any path prefix.

``kiln/models/fields.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Provides field constructor helpers.  Each returns a :class:`~kiln.config.schema.FieldConfig`-compatible object.

.. code-block:: jsonnet

   local field = import 'kiln/models/fields.libsonnet';

   field.uuid("id", primary_key=true)
   field.str("title", unique=false, nullable=false, index=false)
   field.email("address", unique=true)
   field.int("count", nullable=false)
   field.float("price")
   field.bool("active")
   field.datetime("created_at", auto_now_add=true)
   field.datetime("updated_at", auto_now=true)
   field.date("due_on")
   field.json("payload", nullable=true)

Foreign keys use the ``foreign_key`` parameter on ``uuid`` or ``int``:

.. code-block:: jsonnet

   field.uuid("author_id", foreign_key="authors.id")
   // or fully qualified:
   field.uuid("product_id", foreign_key="inventory.products.id")

``kiln/crud/presets.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Provides CRUD preset helpers.  Each returns a
:class:`~kiln.config.schema.CrudConfig`-compatible object.

.. code-block:: jsonnet

   local crud = import 'kiln/crud/presets.libsonnet';

   crud.full({})                                        // all five operations
   crud.full({ require_auth: ["create", "update", "delete"] })
   crud.read_only({})                                   // read + list only
   crud.no_list({})                                     // create/read/update/delete
   crud.write_only({})                                  // create/update/delete

``paginated: true`` (the default) adds ``offset`` and ``limit`` query
parameters to the list endpoint.

``kiln/auth/jwt.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures JWT authentication for the generated ``auth/dependencies.py``.

.. code-block:: jsonnet

   local auth = import 'kiln/auth/jwt.libsonnet';

   auth.jwt({
     secret_env:   "JWT_SECRET",      // env var holding the signing secret
     algorithm:    "HS256",           // HMAC algorithm
     token_url:    "/auth/token",     // URL for the OAuth2 token endpoint
     exclude_paths: ["/docs", "/openapi.json", "/health"],
   })

To use a custom ``get_current_user`` dependency instead of the generated
JWT implementation, set ``get_current_user_fn`` to a dotted import path:

.. code-block:: jsonnet

   auth.jwt({
     secret_env: "JWT_SECRET",
     get_current_user_fn: "myapp.auth.custom.get_current_user",
   })

``kiln/db/databases.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures async PostgreSQL database connections.

.. code-block:: jsonnet

   local db = import 'kiln/db/databases.libsonnet';

   db.postgres("primary", {
     url_env:       "DATABASE_URL",   // env var holding the connection URL
     default:       true,             // use this db when a route omits db_key
     echo:          false,            // log SQL to stderr
     pool_size:     5,                // connections kept open
     max_overflow:  10,               // extra connections above pool_size
     pool_timeout:  30,               // seconds to wait for a connection
     pool_recycle:  -1,               // seconds before recycling; -1 = never
     pool_pre_ping: true,             // test connections before use
   })

When multiple databases are configured, models and views can opt in to a
specific one via ``db_key``:

.. code-block:: jsonnet

   { name: "Report", ..., db_key: "analytics" }
