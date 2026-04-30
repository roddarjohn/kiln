Reference
=========

.. contents:: On this page
   :local:
   :depth: 2

Config schema
-------------

Each target has its own pydantic schema that its config files parse
into:

* ``be`` -- :class:`~be.config.schema.ProjectConfig` (covered in detail
  below).
* ``be_root`` -- :class:`~be_root.config.RootConfig` (:ref:`be_root-schema`).
* ``fe`` -- :class:`~fe.config.ProjectConfig` (:ref:`fe-schema`).
* ``fe_root`` -- :class:`~fe_root.config.RootConfig` (:ref:`fe_root-schema`).

The classes below correspond directly to the fields you write in
the matching ``.jsonnet`` / ``.json`` file for that target.

be config schema
^^^^^^^^^^^^^^^^

.. autoclass:: be.config.schema.ProjectConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.AppConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.App
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.ResourceConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.OperationConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.ModifierConfig
   :members:
   :undoc-members:

.. autodata:: be.config.schema.FieldType
   :no-value:

.. autoclass:: be.config.schema.FieldSpec
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.AuthConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.DatabaseConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.TelemetryConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.RateLimitConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.CommsConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.CommTypeConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.TemplateSource
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.FilterConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.StructuredFilterField
   :members:
   :undoc-members:

.. autodata:: be.config.schema.FilterValueKind
   :no-value:

.. autodata:: be.config.schema.FilterOperator
   :no-value:

.. autoclass:: be.config.schema.LinkConfig
   :members:
   :undoc-members:

.. autodata:: be.config.schema.LinkKind
   :no-value:

.. autoclass:: be.config.schema.SearchConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.OrderConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.PaginateConfig
   :members:
   :undoc-members:

.. autoclass:: be.config.schema.ResourceRegistryConfig
   :members:
   :undoc-members:

Field types
^^^^^^^^^^^

The ``type`` field on :class:`~be.config.schema.FieldSpec` accepts:

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
   * - ``nested``
     - generated sub-schema class
     - read-op schemas (``get`` / ``list``)
     - Dumps a related model inline.  Requires ``model`` and
       ``fields``; see :ref:`nested-fields` below.

.. _nested-fields:

Nested (related-model) fields
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A field with ``type: "nested"`` renders as an inline dump of a
related SQLAlchemy model.  The generator emits a scoped sub-schema
(``{ParentSchema}{FieldPascal}Nested``) plus a sub-serializer, and
attaches a loader to the handler's ``select(...)`` so the
relationship is eagerly loaded before serialization.

Keys on a nested spec:

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Key
     - Meaning
   * - ``model``
     - Dotted import path of the related SQLAlchemy class, e.g.
       ``"blog.models.Author"``.  **Required.**
   * - ``fields``
     - Sub-field list for the dump.  Can itself contain further
       nested entries.  **Required; must be non-empty.**
   * - ``many``
     - ``true`` when the relationship returns a collection.  The
       generated schema wraps the sub-type in ``list[...]`` and the
       serializer list-comprehends over ``obj.{field}``.
   * - ``load``
     - Eager-loading strategy.  ``"selectin"`` (default) issues one
       extra SELECT — safe for both scalar and collection
       relationships and avoids N+1.  ``"joined"`` emits a single
       JOIN (better for one-to-one / many-to-one scalars).
       ``"subquery"`` uses an older correlated-subquery load.
       Mixes freely across nesting levels: a ``"joined"`` outer and
       a ``"selectin"`` inner compose as
       ``joinedload(A.b).selectinload(B.c)``.

Nested fields are supported on read ops (``get`` / ``list``) only.
Write-op request bodies (``create`` / ``update``) must use scalar
fields.

The ``be/fields.libsonnet`` helper library exposes a ``nested``
shortcut for common cases — see :doc:`usage` for examples.

.. _be_root-schema:

be_root config schema
^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: be_root.config.RootConfig
   :members:

.. _fe-schema:

fe config schema
^^^^^^^^^^^^^^^^

.. autoclass:: fe.config.ProjectConfig
   :members:

.. _fe_root-schema:

fe_root config schema
^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: fe_root.config.RootConfig
   :members:

Built-in operations
-------------------

Every built-in operation is registered under its target's own
entry-point group in ``pyproject.toml``: ``be.operations`` for the
``be`` target, ``be_root.operations`` for ``be_root``, and so on.
See :doc:`usage` for what each one generates and :doc:`extending`
for the operation protocol.

The table below covers ``be``'s built-in ops; ``be_root``, ``fe``,
and ``fe_root`` each ship a single project-scope ``RootScaffold`` /
``OpenApiTsConfig`` op (see :ref:`be_root-schema`, :ref:`fe-schema`,
:ref:`fe_root-schema` for the configs that drive them).

.. list-table::
   :header-rows: 1
   :widths: 20 18 18 44

   * - Name
     - Module
     - Scope
     - Description
   * - ``scaffold``
     - :mod:`be.operations.scaffold`
     - project
     - Two project-scope ops live here.  ``Scaffold`` always emits
       ``db/*_session.py``.  ``AuthScaffold`` emits the ``auth/``
       package when ``config.auth`` is set.
   * - ``get`` / ``list`` / ``create`` / ``update`` / ``delete``
     - :mod:`be.operations.get`, :mod:`~be.operations.list`,
       :mod:`~be.operations.create`, :mod:`~be.operations.update`,
       :mod:`~be.operations.delete`
     - resource
     - The five CRUD endpoints.  Each op lives in its own module
       alongside the FastAPI renderer for its output.
   * - ``action``
     - :mod:`be.operations.action`
     - resource
     - Custom action endpoints: ``POST /{pk}/{slug}`` for per-instance
       actions, ``POST /{slug}`` for collection-level actions.
   * - ``auth``
     - :mod:`be.operations.auth`
     - resource
     - Cross-cutting augmenter.  Appends ``current_user`` dependency
       to every CRUD / action handler when ``config.auth`` is set.
   * - ``router``
     - :mod:`be.operations.routing`
     - app
     - Emits ``routes/__init__.py`` for one app, aggregating every
       resource router via ``include_router``.
   * - ``project_router``
     - :mod:`be.operations.routing`
     - project
     - Multi-app projects only.  Emits the top-level
       ``routes/__init__.py`` that mounts each app at its prefix.

Generated file layout
---------------------

The table below summarises every file be can produce.  Paths are
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

Targets
^^^^^^^

.. autoclass:: foundry.target.Target
   :members:

.. autofunction:: foundry.target.discover_targets

.. autoexception:: foundry.errors.CLIError
   :members:
   :undoc-members:

.. autoexception:: foundry.errors.ConfigError
   :members:
   :undoc-members:

.. autoexception:: foundry.errors.GenerationError
   :members:
   :undoc-members:

Engine
^^^^^^

.. autoclass:: foundry.engine.Engine
   :members:

.. autoclass:: foundry.engine.BuildContext
   :members:

Operations
^^^^^^^^^^

.. autofunction:: foundry.operation.operation

.. autofunction:: foundry.operation.load_registry

.. autoclass:: foundry.operation.OperationMeta
   :members:

.. autoclass:: foundry.operation.EmptyOptions
   :members:

.. autoclass:: foundry.operation.OperationRegistry
   :members:

Scopes
^^^^^^

.. autoclass:: foundry.scope.Scope
   :members:

.. autoclass:: foundry.scope.ScopeTree
   :members:

.. autoclass:: foundry.scope.Scoped
   :members:

.. autofunction:: foundry.scope.discover_scopes

.. data:: foundry.scope.PROJECT

    The root scope -- always present in every generation run.

Typed outputs
^^^^^^^^^^^^^

Every operation's ``build`` method returns instances of the types
below.  Framework-agnostic types live in ``foundry.outputs``;
FastAPI-specific output dataclasses live in
``be.operations.types``.

.. autoclass:: foundry.outputs.StaticFile
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.RouteHandler
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.RouteParam
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.SchemaClass
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.Field
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.EnumClass
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.SerializerFn
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.TestCase
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.RouterMount
   :members:
   :undoc-members:

.. autoclass:: be.operations.types.FieldsOptions
   :members:
   :undoc-members:

Render registry
^^^^^^^^^^^^^^^

.. autoclass:: foundry.render.RenderRegistry
   :members:

.. autoclass:: foundry.render.RenderCtx
   :members:

.. autoclass:: foundry.store.BuildStore
   :members:

.. autoclass:: foundry.render.FileFragment
   :members:

.. autoclass:: foundry.render.SnippetFragment
   :members:

.. autoclass:: foundry.render.Fragment
   :members:

.. data:: foundry.render.registry

   Process-wide :class:`RenderRegistry` populated at import time.

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

.. autoclass:: foundry.imports.ImportCollector
   :members:

.. autofunction:: foundry.imports.format_imports

Jinja environment
^^^^^^^^^^^^^^^^^

.. autofunction:: foundry.env.create_jinja_env

.. autofunction:: foundry.env.render_template

Stdlib reference
----------------

The following ``.libsonnet`` files ship inside the be package and
are importable from any config file using the ``be/`` prefix.

``be/auth/jwt.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures JWT authentication.

.. code-block:: jsonnet

   local auth = import 'be/auth/jwt.libsonnet';

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

``be/db/databases.libsonnet``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configures async PostgreSQL connections.

.. code-block:: jsonnet

   local db = import 'be/db/databases.libsonnet';

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

pgqueuer integration
^^^^^^^^^^^^^^^^^^^^

be does not scaffold pgqueuer wiring.  See :doc:`pgqueuer` for
the full guide — the two helpers in :mod:`ingot.queue`
(:func:`ingot.queue.get_queue` for transactional-outbox enqueue,
:func:`ingot.queue.open_worker_driver` for the SQLAlchemy→asyncpg
DSN bridge), the worker-factory pattern, and how to run the
worker with pgqueuer's own CLI.
