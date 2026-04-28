Architecture
============

.. contents:: On this page
   :local:
   :depth: 2

kiln is split into a target-agnostic engine and a set of target plugins
that serve very different audiences:

``foundry``
   A generic, framework-agnostic code-generation engine.  Provides
   the build pipeline, scope discovery, operation protocol, build
   store, render registry, typed output primitives, and the
   ``foundry`` CLI.  Nothing in ``foundry`` knows about FastAPI,
   Pydantic schemas, routes, or any other concrete target; the CLI
   dispatches to plugin-provided :class:`~foundry.target.Target`
   instances discovered via the ``foundry.targets`` entry-point
   group.

``be`` / ``be_root``
   FastAPI / SQLAlchemy code generators registered as ``foundry``
   targets.  ``be`` is the ongoing CRUD / actions / routing
   generator that turns ``config/project.jsonnet`` into routes and
   schemas; ``be_root`` is the one-shot bootstrap that scaffolds
   ``main.py``, ``pyproject.toml``, ``justfile``, and the starter
   ``config/project.jsonnet``.

``fe`` / ``fe_root``
   React / TypeScript counterparts.  ``fe`` is a thin wrapper over
   `@hey-api/openapi-ts <https://heyapi.dev/>`_ -- it translates the
   kiln-side ``config/fe.jsonnet`` into ``openapi-ts.config.ts`` so
   the openapi-ts CLI reads the same source of truth as the rest of
   the project.  ``fe_root`` scaffolds the surrounding yarn / Vite /
   React skeleton, wired to
   `@roddarjohn/glaze <https://github.com/roddarjohn/glaze>`_.

Keeping the engine and the targets apart means you can:

* Build a completely different generator (e.g. a Go server, a gRPC
  skeleton) on ``foundry`` without touching the existing targets.
* Extend any single target without having to know anything about
  the engine internals.

The build pipeline
------------------

Every ``foundry generate`` invocation flows through the same four steps:

.. code-block:: text

   config.jsonnet ──► load ──► ProjectConfig
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

1. **Load** the config file.  :func:`foundry.config.load_config` parses
   JSON or Jsonnet and validates it against :class:`~be.config.schema.ProjectConfig`.

2. **Build** runs every registered operation.
   :class:`~foundry.engine.Engine` walks the config tree scope by
   scope (project → app → resource), running each operation's
   ``build()`` method.  Operations return *typed output objects*
   (``RouteHandler``, ``SchemaClass``, ``StaticFile`` …) which are
   stored in a :class:`~foundry.store.BuildStore`.  Operations can
   also inspect and mutate output produced by earlier operations --
   see :doc:`extending` for an example.

3. **Assemble** turns the build store into real files.  The generic
   assembler (:mod:`foundry.assembler`) groups outputs by target file,
   resolves imports, and renders each outer file template
   (``route.py.j2``, ``schema_outer.py.j2``) around the collected
   snippets.

4. **Write** dumps the file list to disk via
   :func:`foundry.output.write_files`.

Scopes
------

A *scope* is a level in the config tree at which an operation runs.
The engine discovers scopes by inspecting the config model's fields:
any field whose annotation is ``list[SomeBaseModel]`` becomes a scope.

For the current :class:`~be.config.schema.ProjectConfig`:

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
   * - ``app``
     - ``apps: list[App]``
     - One instance per app entry.  Single-app shorthand is
       wrapped into one implicit app during validation.
   * - ``resource``
     - ``resources: list[ResourceConfig]`` (nested under each app)
     - One instance per resource entry.

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
mutable dataclass instances.  The framework-agnostic
:class:`~foundry.outputs.StaticFile` lives in
``foundry.outputs``; the FastAPI-specific output types live in
``be.operations.types``:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Type
     - Represents
   * - :class:`~be.operations.types.RouteHandler`
     - One FastAPI route handler function.
   * - :class:`~be.operations.types.SchemaClass`
     - One Pydantic model class.
   * - :class:`~be.operations.types.SerializerFn`
     - A model-to-schema serializer function.
   * - :class:`~be.operations.types.TestCase`
     - Metadata for a generated pytest test.
   * - :class:`~be.operations.types.RouterMount`
     - One ``include_router`` call in an app/project router.
   * - :class:`~foundry.outputs.StaticFile`
     - A file rendered directly from a template (auth, db session).
   * - :class:`~be.operations.types.EnumClass`
     - An enum definition (used for list-sort fields).

Every type is a plain dataclass, so later operations can freely
inspect and mutate earlier output:

.. code-block:: python

   from be.operations.types import RouteHandler

   for handler in ctx.store.get_by_type(RouteHandler):
       handler.extra_deps.append("user: Annotated[dict, Depends(...)]")

The :class:`~foundry.store.BuildStore` exposes lookup helpers:

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

   from be.operations.types import RouteHandler
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

be's built-in renderers are colocated with their operations:
op-specific :class:`~be.operations.types.RouteHandler` subclasses
register at the bottom of each op module (``be.operations.get``,
``be.operations.list``, …), and the shared cross-cutting renderers
plus fragment-builder helpers live in ``be.operations.renderers``.
All registrations run at module import time and populate the
module-level :data:`foundry.render.registry` singleton; loading
operations via the ``be.operations`` entry-point group is therefore
enough to populate the registry.

Assembler
---------

The assembler (:mod:`foundry.assembler`) is the last step.  It:

1. Walks the build store grouping outputs by target output file
   (e.g. all ``RouteHandler`` objects for one resource go to
   ``routes/{name}.py``).
2. Runs each output through its renderer.
3. Collects and deduplicates imports from
   :attr:`~be.operations.types.RouteHandler.extra_imports` and schema references.
4. Renders the *outer* template
   (``fastapi/route.py.j2``, ``fastapi/schema_outer.py.j2``) with the
   collected snippets and import list.
5. Produces a :class:`~foundry.spec.GeneratedFile` for each output
   file.

The assembler is target-agnostic: it relies only on typed output
objects and the render registry, so any target sharing foundry's
output vocabulary can reuse it.

Discovery via entry points
--------------------------

Each target declares its own entry-point group for the operations it
ships, and foundry walks that group at build time to assemble a
fresh, target-private registry.  Targets installed side-by-side
never see each other's ops: a ``foundry generate --target be`` run
walks ``be.operations`` only, a ``--target fe`` run walks
``fe.operations``, and so on.

be's built-ins live in the package's own ``pyproject.toml`` under
``[project.entry-points."be.operations"]``:

.. code-block:: toml

   [project.entry-points."be.operations"]
   scaffold       = "be.operations.scaffold:Scaffold"
   get            = "be.operations.get:Get"
   list           = "be.operations.list:List"
   create         = "be.operations.create:Create"
   update         = "be.operations.update:Update"
   delete         = "be.operations.delete:Delete"
   action         = "be.operations.action:Action"
   auth           = "be.operations.auth:Auth"
   router         = "be.operations.routing:Router"
   project_router = "be.operations.routing:ProjectRouter"

Third-party packages register their own operations under any
target's group.  Each target also has its own bootstrap counterpart
(``be_root``, ``fe_root``) which registers a single project-scope
op under e.g. ``be_root.operations``.

Targets themselves register under ``foundry.targets``.  A
:class:`~foundry.target.Target` is a frozen dataclass carrying the
target's ``name``, the pydantic ``schema``, a ``template_dir``, the
``operations_entry_point`` group it walks at build time, and an
optional ``jsonnet_stdlib_dir`` -- everything foundry needs to load
config, build the Jinja environment, and assemble output.  All four
targets ship in this repo's ``pyproject.toml``:

.. code-block:: toml

   [project.entry-points."foundry.targets"]
   be      = "be.target:target"
   be_root = "be_root.target:target"
   fe      = "fe.target:target"
   fe_root = "fe_root.target:target"

When exactly one target is installed, ``foundry generate`` picks it
automatically; with multiple, the user selects by name via
``--target``.

Source layout
-------------

.. code-block:: text

   src/
   ├── foundry/              # generic engine + CLI -- target-agnostic
   │   ├── cli.py              # `foundry` CLI (generate/clean/validate)
   │   ├── target.py           # Target dataclass + discover_targets
   │   ├── errors.py           # CLIError, ConfigError, GenerationError
   │   ├── config.py           # load_config (json/jsonnet + validation)
   │   ├── pipeline.py         # generate(config, target)
   │   ├── assembler.py        # generic assemble(store, registry, ctx)
   │   ├── engine.py           # Engine, BuildContext
   │   ├── operation.py        # @operation decorator, OperationMeta
   │   ├── scope.py            # Scope, discover_scopes
   │   ├── render.py           # RenderRegistry, module-level registry
   │   ├── store.py            # BuildStore (with instance tracking)
   │   ├── outputs.py          # StaticFile (target-agnostic output)
   │   ├── naming.py           # Name helper (PascalCase, snake_case, …)
   │   ├── imports.py          # ImportCollector
   │   ├── env.py              # Jinja2 environment factory
   │   ├── spec.py             # GeneratedFile
   │   └── output.py           # write_files
   │
   ├── ingot/                # runtime helpers imported by be-generated apps
   │   ├── auth.py             # JWT helpers, SessionStore protocol
   │   ├── files.py            # presigned-URL upload helpers (boto3)
   │   ├── queue.py            # pgqueuer adapter (get_queue, …)
   │   ├── telemetry.py        # OTel init + per-handler span helpers
   │   ├── filters.py          # list-op filtering helpers
   │   ├── ordering.py         # list-op sorting helpers
   │   ├── pagination.py       # keyset / offset pagination helpers
   │   └── utils.py            # small shared helpers
   │
   ├── be/                   # FastAPI target registered with foundry
   │   ├── target.py           # Target instance (data only)
   │   ├── config/             # Pydantic config schema
   │   ├── operations/         # built-in @operation classes
   │   ├── jsonnet/            # jsonnet stdlib exposed as `be/...`
   │   └── templates/          # Jinja2 templates
   │
   ├── be_root/              # one-shot bootstrap for a be-driven project
   │   ├── target.py
   │   ├── config.py           # RootConfig schema
   │   ├── operations.py       # RootScaffold (project-scope, if_exists=skip)
   │   └── templates/          # main.py / pyproject.toml / justfile / …
   │
   ├── fe/                   # React/TS target -- emits openapi-ts.config.ts
   │   ├── target.py
   │   ├── config.py           # ProjectConfig schema
   │   ├── operations.py       # OpenApiTsConfig
   │   └── templates/          # openapi-ts.config.ts.j2
   │
   └── fe_root/              # one-shot bootstrap for a fe-driven project
       ├── target.py
       ├── config.py           # RootConfig schema
       ├── operations.py       # RootScaffold
       └── templates/          # package.json / justfile / tsconfig / src/…
