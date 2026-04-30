kiln
====

.. warning::

   kiln is in **pre-alpha**. APIs may change between releases.

kiln is a CLI for autogenerating files from templates.  The CLI is the generic
``foundry`` code-generation engine; ``kiln-generator`` ships four targets that
plug into it:

``be``
    FastAPI / SQLAlchemy backend code generator.  Produces routes,
    schemas, serializers, and tests from a Jsonnet config that points
    at your SQLAlchemy models.

``be_root``
    One-shot bootstrap for a ``be``-driven project.  Emits ``main.py``,
    ``pyproject.toml``, ``justfile``, and the starter
    ``config/project.jsonnet``.

``fe``
    React / TypeScript frontend code generator.  A thin wrapper over
    `@hey-api/openapi-ts <https://heyapi.dev/>`_ that translates a
    kiln-side jsonnet config into ``openapi-ts.config.ts`` so the
    generated TypeScript client / React-Query hooks stay in lockstep
    with the same source of truth as the rest of the project.

``fe_root``
    One-shot bootstrap for a ``fe``-driven project.  Emits
    ``package.json``, ``justfile``, ``tsconfig.json``, ``vite.config.ts``,
    and a ``src/`` skeleton wired to
    `@roddarjohn/glaze <https://github.com/roddarjohn/glaze>`_.

Run any target with::

    foundry generate --target <name> --config <path> --out <dir>

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   getting_started
   playground
   usage

.. toctree::
   :maxdepth: 2
   :caption: Guides

   architecture
   extending
   telemetry
   pgqueuer
   comms
   design/filtering

.. toctree::
   :maxdepth: 2
   :caption: Reference

   reference
   api

.. toctree::
   :maxdepth: 1
   :caption: Contributing

   development
   changelog
