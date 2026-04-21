Architecture
============

.. contents:: On this page
   :local:
   :depth: 2

kiln is split into two packages that serve very different audiences:

``foundry`` -- a generic, framework-agnostic code-generation engine.
   Provides the build pipeline, scope discovery, operation protocol,
   build store, render registry, and typed output primitives.  Nothing
   in ``foundry`` knows about FastAPI, Pydantic schemas, routes, or
   any other concrete target.

``kiln`` -- a concrete FastAPI / SQLAlchemy generator built on top of
``foundry``.
   Defines the config schema, ships a set of built-in operations (CRUD,
   actions, scaffolding, routing), a set of renderers backed by Jinja2
   templates, and a CLI (``kiln generate``).

Keeping the two apart means you can:

* Build a completely different generator (e.g. a TypeScript client, a
  Go server, a gRPC skeleton) on ``foundry`` without touching
  ``kiln``.
* Extend the FastAPI generator in ``kiln`` without having to know
  anything about the engine internals.

The build pipeline
------------------

Every ``kiln generate`` invocation flows through the same four steps:

.. code-block:: text

   config.jsonnet ──► load ──► KilnConfig
                                  │
                                  ▼
                           ┌──────────────┐
                           │   Engine     │  ── per-scope, per-instance
                           │   build()    │     build phase
                           └──────┬───────┘
                                  │
                                  ▼
                            BuildStore
                  (typed output objects, keyed by
                   scope / instance / operation)
                                  │
                                  ▼
                           ┌──────────────┐
                           │  Assembler   │  ── group, render, assemble
                           └──────┬───────┘
                                  │
                                  ▼
                         list[GeneratedFile]
                                  │
                                  ▼
                             write_files

1. **Load** the config file.  ``kiln.config.loader.load`` parses JSON
   or Jsonnet and validates it against :class:`~kiln.config.schema.KilnConfig`.

2. **Build** runs every registered operation.
   :class:`~foundry.engine.Engine` walks the config tree scope by
   scope (project → app → resource), running each operation's
   :meth:`build` method.  Operations return *typed output objects*
   (``RouteHandler``, ``SchemaClass``, ``StaticFile`` …) which are
   stored in a :class:`~foundry.render.BuildStore`.  Operations can
   also inspect and mutate output produced by earlier operations --
   see :doc:`extending` for an example.

3. **Assemble** turns the build store into real files.  The FastAPI
   assembler (``kiln.renderers.assembler``) groups outputs by target
   file, resolves imports, and renders each outer file template
   (``route.py.j2``, ``schema_outer.py.j2``) around the collected
   snippets.

4. **Write** dumps the file list to disk via
   :func:`foundry.output.write_files`.

Scopes
------

A *scope* is a level in the config tree at which an operation runs.
The engine discovers scopes by inspecting the config model's fields:
any field whose annotation is ``list[SomeBaseModel]`` becomes a scope.

For the current :class:`~kiln.config.schema.KilnConfig`:

.. list-table::
   :header-rows: 1
   :widths: 15 25 60

   * - Scope
     - Config field
     - Iteration
   * - ``project``
     - (root)
     - Exactly one instance: the full config.
   * - ``database``
     - ``databases: list[DatabaseConfig]``
     - One instance per database entry.
   * - ``resource``
     - ``resources: list[ResourceConfig]``
     - One instance per resource entry.
   * - ``app``
     - ``apps: list[AppRef]``
     - One instance per app entry (multi-app projects only).

An operation declares its scope at decoration time::

   @operation("get", scope="resource")
   class Get:
       ...

The engine runs ``Get.build`` once per resource, so a config with
three resources produces three separate ``Get`` invocations, each with
its own :class:`~foundry.engine.BuildContext`.

Operations
----------

An operation is a class decorated with
:func:`~foundry.operation.operation` that declares:

``name``
    Unique identifier used to look up the operation (and in the
    ``operations`` config list).
``scope``
    The scope at which it runs.
``requires``
    Other operations that must run first within the same scope
    instance.  Gives the engine a dependency graph for topological
    sort.

The class body must provide:

``Options`` *(optional)*
    A :class:`pydantic.BaseModel` subclass describing per-instance
    config.  When absent, defaults to
    :class:`~foundry.operation.EmptyOptions`.

``build(self, ctx, options) -> list``
    Produces typed output objects.  The engine stores them in the
    build store keyed by ``(scope, instance_id, op_name)``.

``when(self, ctx) -> bool`` *(optional)*
    When present, the operation runs only if ``when`` returns
    ``True``.  Operations with a ``when`` hook bypass the
    ``operations`` config list -- they are *cross-cutting* and
    activate themselves.  Auth is the canonical example: it runs
    whenever the project has ``auth`` configured and the resource
    has ``require_auth`` set.

See :doc:`extending` for worked examples.

Typed output objects
--------------------

Operations do not produce strings or files directly.  They produce
mutable dataclass instances in :mod:`foundry.outputs`:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Type
     - Represents
   * - :class:`~foundry.outputs.RouteHandler`
     - One FastAPI route handler function.
   * - :class:`~foundry.outputs.SchemaClass`
     - One Pydantic model class.
   * - :class:`~foundry.outputs.SerializerFn`
     - A model-to-schema serializer function.
   * - :class:`~foundry.outputs.TestCase`
     - Metadata for a generated pytest test.
   * - :class:`~foundry.outputs.RouterMount`
     - One ``include_router`` call in an app/project router.
   * - :class:`~foundry.outputs.StaticFile`
     - A file rendered directly from a template (auth, db session).
   * - :class:`~foundry.outputs.EnumClass`
     - An enum definition (used for list-sort fields).

Every type is a plain dataclass, so later operations can freely
inspect and mutate earlier output:

.. code-block:: python

   from foundry.outputs import RouteHandler

   for handler in ctx.store.get_by_type(RouteHandler):
       handler.extra_deps.append("user: Annotated[dict, Depends(...)]")

The :class:`~foundry.render.BuildStore` exposes lookup helpers:

``get(scope, instance_id, op_name)``
    Outputs from a specific build step.
``get_by_scope(scope, instance_id)``
    All outputs produced for one scope instance.
``get_by_type(cls)``
    All outputs of a given type, across all scopes.

Renderers
---------

A renderer is a function that converts one output object into a code
string.  Renderers live in a
:class:`~foundry.render.RenderRegistry`, keyed by output type:

.. code-block:: python

   from foundry.outputs import RouteHandler
   from foundry.render import RenderRegistry

   registry = RenderRegistry()

   @registry.renders(RouteHandler)
   def render_route(handler, ctx):
       return ctx.env.get_template("fastapi/ops/get.py.j2").render(
           handler=handler,
       )

The ``when`` parameter selects between competing renderers:

.. code-block:: python

   @registry.renders(RouteHandler, when=lambda cfg: cfg.grpc)
   def render_grpc_route(handler, ctx):
       ...  # called instead when config.grpc is truthy

The FastAPI renderers live in ``kiln.renderers.fastapi`` and are
registered via :func:`~kiln.renderers.fastapi.create_registry`.

Assembler
---------

The assembler (``kiln.renderers.assembler``) is the last step.  It:

1. Walks the build store grouping outputs by target output file
   (e.g. all ``RouteHandler`` objects for one resource go to
   ``routes/{name}.py``).
2. Runs each output through its renderer.
3. Collects and deduplicates imports from
   :attr:`RouteHandler.extra_imports` and schema references.
4. Renders the *outer* template
   (``fastapi/route.py.j2``, ``fastapi/schema_outer.py.j2``) with the
   collected snippets and import list.
5. Produces a :class:`~foundry.spec.GeneratedFile` for each output
   file.

The assembler is entirely specific to the FastAPI generator.  A
different target would ship its own assembler.

Discovery via entry points
--------------------------

Operations are loaded from the ``kiln.operations`` entry-point group.
The built-ins are registered in kiln's own ``pyproject.toml``:

.. code-block:: toml

   [project.entry-points."kiln.operations"]
   scaffold       = "kiln.operations.scaffold:Scaffold"
   utils          = "kiln.operations.infra:Utils"
   get            = "kiln.operations.get:Get"
   list           = "kiln.operations.list:List"
   create         = "kiln.operations.create:Create"
   update         = "kiln.operations.update:Update"
   delete         = "kiln.operations.delete:Delete"
   action         = "kiln.operations.action:Action"
   auth           = "kiln.operations.auth:Auth"
   router         = "kiln.operations.routing:Router"
   project_router = "kiln.operations.routing:ProjectRouter"

Third-party packages register their own operations under the same
group.  ``kiln generate`` discovers all installed operations at
startup.

Source layout
-------------

.. code-block:: text

   src/
   ├── foundry/              # generic engine -- target-agnostic
   │   ├── engine.py           # Engine, BuildContext
   │   ├── operation.py        # @operation decorator, OperationMeta
   │   ├── scope.py            # Scope, discover_scopes
   │   ├── render.py           # @renders, RenderRegistry, BuildStore
   │   ├── outputs.py          # RouteHandler, SchemaClass, StaticFile, ...
   │   ├── naming.py           # Name helper (PascalCase, snake_case, …)
   │   ├── imports.py          # ImportCollector
   │   ├── env.py              # Jinja2 environment factory
   │   ├── spec.py             # GeneratedFile
   │   └── output.py           # write_files
   │
   └── kiln/                   # FastAPI generator on top of foundry
       ├── cli.py              # `kiln` CLI
       ├── config/             # Pydantic config schema + loader
       ├── operations/         # built-in @operation classes
       │   ├── get.py          # one file per op: @operation class +
       │   ├── list.py         #   RouteHandler subclass + FastAPI
       │   ├── create.py       #   renderer registration
       │   ├── update.py
       │   ├── delete.py
       │   ├── action.py
       │   ├── auth.py
       │   ├── scaffold.py
       │   ├── infra.py
       │   ├── routing.py
       │   ├── _shared.py      # helpers shared by the per-op modules
       │   ├── _introspect.py  # action-fn introspection
       │   └── _list_config.py # FilterConfig, OrderConfig, PaginateConfig
       ├── renderers/          # @renders implementations
       │   ├── fastapi.py      # renderer registration
       │   ├── assembler.py    # grouping, imports, outer templates
       │   └── generate.py     # orchestrates engine + assembler
       ├── templates/          # Jinja2 templates
       │   ├── fastapi/        # ops/, schema_parts/, outer templates
       │   └── init/           # auth + db session templates
       └── generators/
           ├── _env.py         # shared Jinja2 env
           └── _helpers.py     # PYTHON_TYPES, resolve_db_session, …
