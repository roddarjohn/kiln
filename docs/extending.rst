Extending kiln
==============

Kiln is designed to be extended.  There are two ways to add new
generation capabilities:

1. **Entry-point generators** — a Python package that plugs in via
   ``pyproject.toml``.  This is the recommended approach for
   generators you want to reuse across projects or share publicly.

2. **Jsonnet stdlib additions** — pure-config helpers that compose
   existing kiln primitives.  No Python required.

Writing a custom generator
--------------------------

A generator is any class that satisfies the
:class:`~kiln.generators.base.Generator` protocol:

.. code-block:: python

   from kiln.config.schema import KilnConfig
   from kiln.generators.base import GeneratedFile


   class TypeScriptClientGenerator:
       """Generates a TypeScript API client from the kiln config."""

       @property
       def name(self) -> str:
           return "typescript_client"

       def can_generate(self, config: KilnConfig) -> bool:
           # Only run when models with CRUD are present
           return any(m.crud is not None for m in config.models)

       def generate(self, config: KilnConfig) -> list[GeneratedFile]:
           files = []
           for model in config.models:
               if model.crud is None:
                   continue
               files.append(GeneratedFile(
                   path=f"client/{model.name.lower()}.ts",
                   content=_render_ts_client(model),
               ))
           return files

The :class:`~kiln.generators.base.GeneratedFile` ``overwrite`` flag
controls whether re-running ``kiln generate`` replaces existing output:

* ``overwrite=True`` (default) — always refresh on re-generation.
* ``overwrite=False`` — write only if the file does not already exist.
  Use this for stubs the developer is expected to fill in.

Registering via entry points
----------------------------

Add the generator to your package's ``pyproject.toml``:

.. code-block:: toml

   [project.entry-points."kiln.generators"]
   typescript = "my_package.generators:TypeScriptClientGenerator"

Kiln discovers all installed generators in this group automatically
when :meth:`~kiln.generators.registry.GeneratorRegistry.default` is
called (which is what ``kiln generate`` uses).

Multiple generators can be registered from the same package:

.. code-block:: toml

   [project.entry-points."kiln.generators"]
   typescript = "my_package:TypeScriptClientGenerator"
   openapi    = "my_package:OpenAPISpecGenerator"

Customising CRUD operations
----------------------------

The built-in :class:`~kiln.generators.fastapi.resource.ResourceGenerator`
uses a **pipeline** of composable operations.  Each CRUD action (get,
list, create, update, delete) is a separate
:class:`~kiln.generators.fastapi.operations.Operation` that contributes
schema classes and route handlers to the generated files.

You can add, remove, or replace operations to customise the output.

Adding a custom operation
~~~~~~~~~~~~~~~~~~~~~~~~~

Create a class that satisfies the
:class:`~kiln.generators.fastapi.operations.Operation` protocol:

.. code-block:: python

   from kiln.generators.fastapi.operations import (
       Operation,
       SharedContext,
       default_operations,
   )
   from kiln.generators.fastapi.pipeline import ResourcePipeline
   from kiln.generators.fastapi.resource import ResourceGenerator
   from kiln.generators.base import FileSpec
   from kiln.config.schema import ResourceConfig


   class BulkCreateOperation:
       """POST /bulk — create multiple resources at once."""

       name = "bulk_create"

       def enabled(self, resource: ResourceConfig) -> bool:
           return resource.create is not False

       def contribute(
           self,
           specs: dict[str, FileSpec],
           resource: ResourceConfig,
           ctx: SharedContext,
       ) -> None:
           schema = specs["schema"]
           route = specs["route"]

           # Add a BulkCreateRequest schema class
           schema.imports.add_from("pydantic", "BaseModel")
           snippet = f'''
   class {ctx.model.pascal}BulkCreateRequest(BaseModel):
       """Bulk create request."""

       items: list[{ctx.model.suffixed("CreateRequest")}]
   '''
           schema.context["schema_classes"].append(snippet)
           schema.exports.append(
               ctx.model.suffixed("BulkCreateRequest")
           )

           # Add a POST /bulk route handler
           route.imports.add_from("sqlalchemy", "insert")
           route.imports.add_from(
               ctx.model_module, ctx.model.pascal
           )
           handler = f'''
   @router.post("/bulk", status_code=status.HTTP_201_CREATED)
   async def bulk_create_{ctx.model.lower}(
       payload: {ctx.model.suffixed("BulkCreateRequest")},
       db: Annotated[AsyncSession, Depends({ctx.get_db_fn})],
   ):
       for item in payload.items:
           stmt = insert({ctx.model.pascal}).values(
               **item.model_dump()
           )
           await db.execute(stmt)
       await db.commit()
   '''
           route.context["route_handlers"].append(handler)

Then wire it into a custom pipeline:

.. code-block:: python

   pipeline = ResourcePipeline(
       operations=[
           *default_operations(),
           BulkCreateOperation(),
       ]
   )
   gen = ResourceGenerator(pipeline=pipeline)

Removing or replacing operations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To remove an operation, filter it out of the default list:

.. code-block:: python

   ops = [
       op for op in default_operations()
       if op.name != "delete"
   ]
   pipeline = ResourcePipeline(operations=ops)

To replace one, swap it in-place:

.. code-block:: python

   ops = default_operations()
   ops = [
       MyCustomGetOperation() if op.name == "get" else op
       for op in ops
   ]
   pipeline = ResourcePipeline(operations=ops)

Using a custom pipeline with entry points
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To distribute a custom pipeline as a package, wrap it in a
:class:`~kiln.generators.base.Generator`:

.. code-block:: python

   class MyResourceGenerator:
       @property
       def name(self) -> str:
           return "resources"  # replaces the built-in one

       def can_generate(self, config):
           return bool(config.resources)

       def generate(self, config):
           pipeline = ResourcePipeline(
               operations=[
                   *default_operations(),
                   BulkCreateOperation(),
               ]
           )
           files = []
           for resource in config.resources:
               files.extend(pipeline.build(resource, config))
           return files

Register it the same way as any other generator:

.. code-block:: toml

   [project.entry-points."kiln.generators"]
   resources = "my_package:MyResourceGenerator"

Because it uses the same ``name`` (``"resources"``), it replaces the
built-in generator.

How operations work
~~~~~~~~~~~~~~~~~~~

Each operation receives a ``specs: dict[str, FileSpec]`` bag.
Built-in keys are ``"schema"``, ``"route"``, and optionally
``"serializer"`` — but extensions can add any key they like
(e.g. ``"test"``, ``"client"``).

Operations mutate specs by:

- Looking up specs by key: ``schema = specs["schema"]``
- Adding imports via ``spec.imports.add_from("module", "name")``
- Appending content to context lists (e.g.
  ``spec.context["schema_classes"]``)
- Registering export names in ``spec.exports``
- Creating entirely new specs: ``specs["myfile"] = FileSpec(...)``

After all operations run, the pipeline automatically wires cross-file
imports: every spec's exports are made available to every other spec
via ``from module import ...`` lines.

The ``SetupOperation`` (first in ``default_operations()``) creates the
base ``"schema"`` and ``"route"`` specs.  Custom operations that add
new file types should create their own specs in ``contribute()``.

Testing a custom generator
--------------------------

Use the registry directly in tests:

.. code-block:: python

   from kiln.config.schema import KilnConfig
   from kiln.generators.registry import GeneratorRegistry
   from my_package import TypeScriptClientGenerator

   def test_my_generator():
       registry = GeneratorRegistry()
       registry.register(TypeScriptClientGenerator())

       config = KilnConfig(...)
       files = registry.run(config)
       assert any(f.path.endswith(".ts") for f in files)

Jsonnet stdlib extensions
-------------------------

To share config patterns without writing Python, add ``.libsonnet``
files alongside your config and import them:

.. code-block:: jsonnet

   // shared/soft_delete.libsonnet
   // Adds standard soft-delete fields to any model.
   {
     fields:: [
       { name: "deleted_at", type: "datetime", nullable: true },
       { name: "deleted_by", type: "uuid",     nullable: true },
     ],
   }

.. code-block:: jsonnet

   // myapp.jsonnet
   local sd = import 'shared/soft_delete.libsonnet';
   local field = import 'kiln/models/fields.libsonnet';

   {
     models: [{
       name: "Order",
       table: "orders",
       fields: [field.uuid("id", primary_key=true)] + sd.fields,
       ...
     }],
   }
