Extending kiln
==============

.. contents:: On this page
   :local:
   :depth: 2

kiln is designed to be extended at three levels:

1. **Add an operation.**  The most common extension -- contribute a
   new CRUD-like endpoint, a cross-cutting concern (auth, rate
   limiting, caching), or a completely new file type.
2. **Swap a renderer.**  Replace or augment how an existing output
   type is turned into code, without touching the operation that
   produces it.
3. **Ship a new target.**  Build a generator for a different
   framework entirely by using ``foundry`` directly -- no
   dependency on ``kiln``'s FastAPI-specific bits.

This document covers all three, in increasing order of ambition.
For background on the architecture, see :doc:`architecture`.

Adding an operation
-------------------

An operation is a class decorated with
:func:`~foundry.operation.operation` that produces typed output
objects in its :meth:`build` method.  The engine takes care of
scheduling: scope walking, dependency ordering, and options parsing.

Step 1 -- write the class
^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from pydantic import BaseModel

   from foundry.engine import BuildContext
   from foundry.operation import operation
   from foundry.outputs import RouteHandler, RouteParam


   @operation("bulk_create", scope="resource", requires=["create"])
   class BulkCreate:
       """POST /bulk -- insert many resources in one request."""

       class Options(BaseModel):
           max_items: int = 100

       def build(
           self,
           ctx: BuildContext,
           options: "Options",
       ) -> list[object]:
           model = ctx.instance.model.rpartition(".")[-1]
           handler = RouteHandler(
               method="post",
               path="/bulk",
               function_name=f"bulk_create_{model.lower()}",
               op_name="bulk_create",
               params=[
                   RouteParam(
                       name="payload",
                       annotation=f"list[{model}CreateRequest]",
                   ),
               ],
               body_lines=[
                   f"if len(payload) > {options.max_items}:",
                   "    raise HTTPException(413)",
                   f"stmt = insert({model}).values(",
                   "    [p.model_dump() for p in payload]",
                   ")",
                   "await db.execute(stmt)",
                   "await db.commit()",
               ],
               status_code=201,
           )
           return [handler]

A few things to notice:

* The class name is irrelevant to the engine -- the name from the
  ``@operation(...)`` decorator is what matters.
* ``scope="resource"`` means ``build()`` runs once per
  :class:`~kiln.config.schema.ResourceConfig`.
* ``requires=["create"]`` ensures the ``create`` operation builds
  first, so its schemas are available in the build store before this
  runs (useful if you want to inspect or extend them).
* ``Options`` is optional.  When omitted, operations receive
  :class:`~foundry.operation.EmptyOptions`.

Step 2 -- register via entry point
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Add your operation to your package's ``pyproject.toml``:

.. code-block:: toml

   [project.entry-points."kiln.operations"]
   bulk_create = "my_pkg.ops:BulkCreate"

``kiln generate`` discovers all installed operations at startup, so
as long as your package is ``pip install``\ ed alongside kiln the
operation is available.

Step 3 -- opt resources in
^^^^^^^^^^^^^^^^^^^^^^^^^^

Add the operation name to the resource's ``operations`` list:

.. code-block:: jsonnet

   {
     resources: [{
       model: "myapp.models.Article",
       operations: [
         "get", "list", "create", "update", "delete",
         { name: "bulk_create", max_items: 50 },
       ],
     }],
   }

Options come from the extra keys on the operation entry.  Pydantic
validates them against the ``Options`` class.

Cross-cutting operations with ``when()``
----------------------------------------

Some operations should not appear in the user's ``operations`` list
at all -- they activate themselves based on config and augment other
operations' output.  Auth is the canonical example: when
``config.auth`` is set, auth silently appends a dependency to every
CRUD handler.

Declare a ``when()`` method instead of relying on the opt-in list:

.. code-block:: python

   from foundry.engine import BuildContext
   from foundry.operation import operation
   from foundry.outputs import RouteHandler


   @operation(
       "rate_limit",
       scope="resource",
       requires=["get", "list", "create", "update", "delete"],
   )
   class RateLimit:
       """Add a rate-limit decorator to every write handler."""

       def when(self, ctx: BuildContext) -> bool:
           return getattr(ctx.config, "rate_limit", None) is not None

       def build(self, ctx, _options):
           limit = ctx.config.rate_limit
           for h in ctx.store.get_by_scope(
               ctx.scope.name, ctx.instance_id,
           ):
               if isinstance(h, RouteHandler) and h.method != "get":
                   h.add_decorator(
                       f"@limiter.limit('{limit}')",
                   )
                   h.extra_imports.append(
                       ("myapp.rate_limit", "limiter"),
                   )
           return []

Three important properties:

* **``when`` bypasses the opt-in list.**  Cross-cutting operations run
  whenever their predicate says so, regardless of the user's
  ``operations`` config.  Users don't have to remember to opt in.
* **``requires`` orders it after producers.**  Listing the CRUD
  operations in ``requires`` guarantees their output exists in the
  build store by the time ``build`` runs.
* **The build method returns ``[]``.**  Augmenting operations mutate
  existing objects in place; they produce no new outputs of their
  own.

See ``src/kiln/operations/auth.py`` for the real-world auth
implementation.

Augmenting vs producing
-----------------------

``foundry`` deliberately has one mechanism -- operations -- for
both "produce new output" and "modify earlier output".  This keeps
the execution model simple and uniform:

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Role
     - Returns from ``build``
     - Typical ``requires``
   * - Producer
     - New output objects
     - Nothing (or earlier producers whose output you depend on)
   * - Augmenter
     - ``[]`` (nothing new)
     - All producers whose output you want to mutate

An operation can also do both: produce new objects *and* tweak
earlier ones in the same ``build`` call.

Mutating output objects
-----------------------

Every type in :mod:`foundry.outputs` is a mutable dataclass with
helpers for safe modification:

.. code-block:: python

   from foundry.outputs import RouteHandler, SchemaClass

   for handler in ctx.store.get_by_type(RouteHandler):
       handler.add_decorator("@cache(ttl=60)")
       handler.prepend_body("log.info('cached-endpoint-hit')")
       handler.extra_imports.append(("myapp.cache", "cache"))

   for schema in ctx.store.get_by_type(SchemaClass):
       if schema.name.endswith("Resource"):
           schema.add_field("cached_at", "datetime", optional=True)

:meth:`RouteHandler.extra_imports` is the recommended way to add
imports.  The assembler merges every handler's ``extra_imports`` into
the route file's top-of-file import block automatically.

Swapping a renderer
-------------------

Every output type has a default renderer in
``kiln.renderers.fastapi``.  You can override or supplement these
without changing the operation that produces the output.

Register an additional renderer with a ``when`` predicate.  The
registry tries registrations in order and uses the first whose
predicate matches:

.. code-block:: python

   from foundry.outputs import RouteHandler
   from foundry.render import RenderRegistry

   def register_my_renderers(registry: RenderRegistry) -> None:

       @registry.renders(
           RouteHandler,
           when=lambda cfg: getattr(cfg, "use_async_retry", False),
       )
       def render_retry_handler(handler, ctx):
           # custom Jinja template that wraps the body in a retry loop
           return ctx.env.get_template(
               "my_pkg/retry_handler.j2",
           ).render(h=handler)

Register the predicate-guarded renderer *first* (before the default
unguarded one) if you want it to win when the flag is on.

To plug new renderers in, write your own equivalent of
:func:`kiln.renderers.fastapi.create_registry` and wire it into your
own ``generate()`` entry point.  There is no entry-point group for
renderers because there can only be one registry per generation run.

Adding a new output type
------------------------

You are not limited to the built-in output types.  A plugin can
define its own dataclass, register a renderer for it, and have
operations emit instances of it.

.. code-block:: python

   from dataclasses import dataclass, field
   from foundry.operation import operation
   from foundry.render import RenderRegistry


   @dataclass
   class GraphQLField:
       name: str
       gql_type: str
       resolver: str | None = None


   @operation("graphql_fields", scope="resource")
   class GraphQLFields:
       def build(self, ctx, _options):
           return [
               GraphQLField(name=f.name, gql_type=f.type.upper())
               for f in ctx.instance.fields
           ]


   def register(registry: RenderRegistry) -> None:

       @registry.renders(GraphQLField)
       def render_gql(field, _ctx):
           return f"{field.name}: {field.gql_type}"

The assembler only knows how to group the built-in types.  A plugin
that introduces a new type is also responsible for extending the
assembler (or shipping its own) so the renderer output ends up in
the right file.

Building on ``foundry`` directly
-----------------------------------

If you are generating code for a target that has nothing to do with
FastAPI (a Go CLI, a Terraform module, a gRPC service), skip the
``kiln`` package entirely and use ``foundry``:

.. code-block:: python

   from foundry.engine import Engine
   from foundry.render import RenderCtx, RenderRegistry
   from foundry.env import create_jinja_env

   def generate_my_thing(config):
       engine = Engine(operations=[...])
       store = engine.build(config)

       registry = RenderRegistry()
       register_my_renderers(registry)

       env = create_jinja_env("my_pkg", "templates")
       ctx = RenderCtx(env=env, config=config)

       files = []
       for obj in store.all_items():
           content = registry.render(obj, ctx)
           files.append(GeneratedFile(path=..., content=content))
       return files

You need:

* A Pydantic config schema for your target.
* Your own operations.
* Your own renderers, registry, and assembler.
* A Jinja2 template directory (or a different renderer backend).

Everything in ``foundry`` is target-agnostic and reusable.

Operation validation
--------------------

Per-operation options are validated by Pydantic at config-load time
because they are parsed into the operation's ``Options`` model.  Any
cross-field validation belongs in a Pydantic
``@model_validator(mode="after")`` on that model:

.. code-block:: python

   from pydantic import BaseModel, model_validator


   class BulkCreateOptions(BaseModel):
       max_items: int = 100
       batch_size: int = 10

       @model_validator(mode="after")
       def _check(self):
           if self.batch_size > self.max_items:
               raise ValueError(
                   "batch_size must be <= max_items",
               )
           return self


   @operation("bulk_create", scope="resource")
   class BulkCreate:
       Options = BulkCreateOptions
       ...

Errors raised during config loading are reported to the user before
generation begins.

Testing your extension
----------------------

Run your operations through the engine directly -- no CLI needed:

.. code-block:: python

   from kiln.config.schema import KilnConfig, ResourceConfig
   from foundry.engine import Engine
   from foundry.outputs import RouteHandler

   from my_pkg.ops import BulkCreate


   def test_bulk_create_produces_handler():
       cfg = KilnConfig(
           resources=[
               ResourceConfig(
                   model="myapp.Article",
                   operations=["bulk_create"],
               ),
           ],
       )
       engine = Engine(operations=[BulkCreate])
       store = engine.build(cfg)

       handlers = store.get_by_type(RouteHandler)
       assert any(h.path == "/bulk" for h in handlers)

For full end-to-end coverage, load a fixture config via
:func:`kiln.config.loader.load` and pass it through
:func:`kiln.renderers.generate.generate` to get back the full list of
:class:`~foundry.spec.GeneratedFile` objects.
