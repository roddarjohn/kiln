Reference
=========

.. contents:: On this page
   :local:
   :depth: 2

Config schema
-------------

All config files parse into a :class:`~kiln.config.schema.KilnConfig`
instance.  The classes below correspond directly to the fields you
write in ``.jsonnet`` / ``.json``.

.. autoclass:: kiln.config.schema.KilnConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.ResourceConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.OperationConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.FieldSpec
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.AuthConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.DatabaseConfig
   :members:
   :undoc-members:

.. autoclass:: kiln.config.schema.AppRef
   :members:
   :undoc-members:

Field types
^^^^^^^^^^^

The ``type`` field on :class:`~kiln.config.schema.FieldSpec` accepts:

.. list-table::
   :header-rows: 1
   :widths: 15 25 30 30

   * - Type
     - Python annotation
     - Used in
     - Notes
   * - ``uuid``
     - ``uuid.UUID``
     - request/response schemas, pk
     - Default for primary keys.
   * - ``str``
     - ``str``
     - schemas, action params
     -
   * - ``email``
     - ``str``
     - schemas
     - Added ``pydantic.EmailStr`` validation.
   * - ``int``
     - ``int``
     - schemas, pk
     -
   * - ``float``
     - ``float``
     - schemas
     -
   * - ``bool``
     - ``bool``
     - schemas
     -
   * - ``datetime``
     - ``datetime.datetime``
     - schemas
     -
   * - ``date``
     - ``datetime.date``
     - schemas
     -
   * - ``json``
     - ``dict[str, Any]``
     - schemas
     -

Built-in operations
-------------------

Every built-in operation is registered under the ``kiln.operations``
entry-point group in kiln's own ``pyproject.toml``.  See :doc:`usage`
for what each one generates and :doc:`extending` for the operation
protocol.

.. list-table::
   :header-rows: 1
   :widths: 20 18 18 44

   * - Name
     - Module
     - Scope
     - Description
   * - ``scaffold``
     - :mod:`kiln.operations.scaffold`
     - project
     - Emits ``db/*_session.py`` and (if ``auth`` is configured)
       ``auth/dependencies.py`` + ``auth/router.py``.
   * - ``get`` / ``list`` / ``create`` / ``update`` / ``delete``
     - :mod:`kiln.operations.get`, :mod:`~kiln.operations.list`,
       :mod:`~kiln.operations.create`, :mod:`~kiln.operations.update`,
       :mod:`~kiln.operations.delete`
     - resource
     - The five CRUD endpoints.  Each op lives in its own module
       alongside the FastAPI renderer for its output.
   * - ``action``
     - :mod:`kiln.operations.action`
     - resource
     - Custom action endpoints: ``POST /{pk}/{slug}`` for per-instance
       actions, ``POST /{slug}`` for collection-level actions.
   * - ``auth``
     - :mod:`kiln.operations.auth`
     - resource
     - Cross-cutting augmenter.  Appends ``current_user`` dependency
       to every CRUD / action handler when ``config.auth`` is set.
   * - ``router``
     - :mod:`kiln.operations.routing`
     - app
     - Emits ``routes/__init__.py`` for one app, aggregating every
       resource router via ``include_router``.
   * - ``project_router``
     - :mod:`kiln.operations.routing`
     - project
     - Multi-app projects only.  Emits the top-level
       ``routes/__init__.py`` that mounts each app at its prefix.

Generated file layout
---------------------

The table below summarises every file kiln can produce.  Paths are
relative to the ``--out`` directory (or to the config's
``package_prefix`` when ``--out`` is omitted).  ``{module}`` is the
app's ``module`` config field.  ``{name}`` is the lowercase,
snake-cased model name.

.. list-table::
   :header-rows: 1
   :widths: 50 35 15

   * - Path
     - Produced by
     - Overwrite
   * - ``db/__init__.py``
     - ``scaffold``
     - Yes
   * - ``db/{db_key}_session.py`` (or ``db/session.py``)
     - ``scaffold``
     - Yes
   * - ``auth/__init__.py``
     - ``scaffold``
     - Yes
   * - ``auth/dependencies.py``
     - ``scaffold``
     - Yes
   * - ``auth/router.py``
     - ``scaffold``
     - Yes
   * - ``{module}/schemas/{name}.py``
     - ``get`` / ``list`` / ``create`` / ``update``
     - Yes
   * - ``{module}/serializers/{name}.py``
     - ``get`` / ``list``
     - Yes
   * - ``{module}/routes/{name}.py``
     - CRUD + ``action``
     - Yes
   * - ``{module}/routes/__init__.py``
     - ``router``
     - Yes
   * - ``{module}/tests/test_{name}.py``
     - CRUD + ``action`` (when ``generate_tests: true``)
     - Yes
   * - ``routes/__init__.py``
     - ``project_router``
     - Yes

Every file is overwritten on every generation run.

foundry API
-------------

Engine
^^^^^^

.. autoclass:: foundry.engine.Engine
   :members:

.. autoclass:: foundry.engine.BuildContext
   :members:

Operations
^^^^^^^^^^

.. autofunction:: foundry.operation.operation

.. autoclass:: foundry.operation.OperationMeta
   :members:

.. autoclass:: foundry.operation.EmptyOptions
   :members:

.. autofunction:: foundry.operation.topological_sort

Scopes
^^^^^^

.. autoclass:: foundry.scope.Scope
   :members:

.. autofunction:: foundry.scope.discover_scopes

.. data:: foundry.scope.PROJECT

    The root scope -- always present in every generation run.

Typed outputs
^^^^^^^^^^^^^

Every operation's ``build`` method returns instances of the types
below.  All are mutable dataclasses.

.. autoclass:: foundry.outputs.RouteHandler
   :members:

.. autoclass:: foundry.outputs.RouteParam
   :members:

.. autoclass:: foundry.outputs.SchemaClass
   :members:

.. autoclass:: foundry.outputs.Field
   :members:

.. autoclass:: foundry.outputs.EnumClass
   :members:

.. autoclass:: foundry.outputs.SerializerFn
   :members:

.. autoclass:: foundry.outputs.TestCase
   :members:

.. autoclass:: foundry.outputs.RouterMount
   :members:

.. autoclass:: foundry.outputs.StaticFile
   :members:

Render registry
^^^^^^^^^^^^^^^

.. autoclass:: foundry.render.RenderRegistry
   :members:

.. autoclass:: foundry.render.RenderCtx
   :members:

.. autoclass:: foundry.render.BuildStore
   :members:

Output
^^^^^^

.. autoclass:: foundry.spec.GeneratedFile
   :members:

.. autofunction:: foundry.output.write_files

Naming and imports
^^^^^^^^^^^^^^^^^^

.. autoclass:: foundry.naming.Name
   :members:

.. autofunction:: foundry.naming.prefix_import

.. autofunction:: foundry.naming.split_dotted_class

.. autoclass:: foundry.imports.ImportCollector
   :members:

Jinja environment
^^^^^^^^^^^^^^^^^

.. autofunction:: foundry.env.create_jinja_env

.. autofunction:: foundry.env.render_snippet

Stdlib reference
----------------

The following ``.libsonnet`` files ship inside the kiln package and
are importable from any config file using the ``kiln/`` prefix.

``kiln/auth/jwt.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures JWT authentication.

.. code-block:: jsonnet

   local auth = import 'kiln/auth/jwt.libsonnet';

   auth.jwt({
     secret_env:            "JWT_SECRET",
     algorithm:             "HS256",
     token_url:             "/auth/token",
     exclude_paths:         ["/docs", "/openapi.json", "/health"],
     verify_credentials_fn: "myapp.auth.verify_credentials",
   })

To supply a custom ``get_current_user`` dependency instead of the
generated JWT flow, set ``get_current_user_fn`` to a dotted import
path.  In that case ``verify_credentials_fn`` is not required.

``kiln/db/databases.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures async PostgreSQL connections.

.. code-block:: jsonnet

   local db = import 'kiln/db/databases.libsonnet';

   db.postgres("primary", {
     url_env:       "DATABASE_URL",
     default:       true,
     echo:          false,
     pool_size:     5,
     max_overflow:  10,
     pool_timeout:  30,
     pool_recycle:  -1,
     pool_pre_ping: true,
   })

Resources that omit ``db_key`` use the database with ``default: true``.
