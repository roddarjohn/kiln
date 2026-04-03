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
           # Only run when resources with operations are present
           return any(r.operations for r in config.resources)

       def generate(self, config: KilnConfig) -> list[GeneratedFile]:
           files = []
           for resource in config.resources:
               if not resource.operations:
                   continue
               files.append(GeneratedFile(
                   path=f"client/{resource.model.split('.')[-1].lower()}.ts",
                   content=_render_ts_client(resource),
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

Operations are discovered via the ``kiln.operations`` entry-point group,
using the same mechanism as generators.  You can add custom operations
by registering them in your package's ``pyproject.toml``.

Adding a custom operation
~~~~~~~~~~~~~~~~~~~~~~~~~

Create a class that satisfies the
:class:`~kiln.generators.fastapi.operations.Operation` protocol:

.. code-block:: python

   from kiln.config.schema import OperationConfig, ResourceConfig
   from kiln.generators.fastapi.operations import SharedContext
   from kiln.generators.base import FileSpec


   class BulkCreateOperation:
       """POST /bulk — create multiple resources at once."""

       name = "bulk_create"

       def validate(self, op_config: OperationConfig) -> None:
           max_items = op_config.options.get("max_items", 100)
           if max_items < 1:
               raise ValueError("max_items must be >= 1")

       def contribute(
           self,
           specs: dict[str, FileSpec],
           resource: ResourceConfig,
           ctx: SharedContext,
           op_config: OperationConfig,
       ) -> None:
           schema = specs["schema"]
           route = specs["route"]
           max_items = op_config.options.get("max_items", 100)

           # Add a BulkCreateRequest schema class
           schema.imports.add_from("pydantic", "BaseModel")
           snippet = f'''
   class {ctx.model.pascal}BulkCreateRequest(BaseModel):
       """Bulk create request (max {max_items} items)."""

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

Register it as an entry point:

.. code-block:: toml

   [project.entry-points."kiln.operations"]
   bulk_create = "my_package.ops:BulkCreateOperation"

Then use it in your config:

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

Configuring operations
~~~~~~~~~~~~~~~~~~~~~~

Operations are configured at three levels, with more specific levels
overriding more general ones:

1. **Project-level** — ``operations`` on the top-level ``KilnConfig``
   sets the default for all apps.
2. **App-level** — ``operations`` on an app's ``KilnConfig`` overrides
   the project default.
3. **Resource-level** — ``operations`` on a ``ResourceConfig`` overrides
   the app default.

When a resource does not specify ``operations``, it inherits from its
parent config.  Use Jsonnet array concatenation to extend rather than
replace:

.. code-block:: jsonnet

   // Project-level defaults
   {
     operations: ["get", "list", "create", "update", "delete"],

     apps: [{
       config: {
         module: "blog",
         resources: [{
           model: "blog.models.Article",
           // Override: only get and list, plus a custom action
           operations: [
             "get", "list",
             { name: "publish", fn: "blog.actions.publish" },
           ],
         }],
       },
       prefix: "/blog",
     }],
   }

Each operation entry can be:

* A **string** — built-in operation name, e.g. ``"get"``.
* An **object** with ``name`` — operation with options:

  .. code-block:: jsonnet

     { name: "create", fields: [...], require_auth: true }

* An **action** — object with ``name`` and ``fn``:

  .. code-block:: jsonnet

     { name: "publish", fn: "blog.actions.publish", params: [...] }

* A **custom operation** — object with ``class``:

  .. code-block:: jsonnet

     { name: "bulk_create", "class": "my_pkg.BulkCreateOp", max: 100 }

Operation validation
~~~~~~~~~~~~~~~~~~~~

Each operation can validate its configuration in the ``validate()``
method, which runs before any ``contribute()`` methods.  This ensures
all configuration errors are reported before generation begins:

.. code-block:: python

   class BulkCreateOperation:
       name = "bulk_create"

       def validate(self, op_config: OperationConfig) -> None:
           if "max_items" not in op_config.options:
               raise ValueError("bulk_create requires 'max_items'")

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

The ``SetupOperation`` (always run internally) creates the base
``"schema"`` and ``"route"`` specs.  Custom operations that add new
file types should create their own specs in ``contribute()``.

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
