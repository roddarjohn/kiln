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
