"""fe: a foundry target that emits ``openapi-ts.config.ts``.

The actual TypeScript codegen is delegated to
`@hey-api/openapi-ts <https://heyapi.dev/>`_, which produces
typed clients and (via the ``@tanstack/react-query`` plugin)
React-Query hooks straight from an OpenAPI 3.x spec.

The fe target's job is small: keep the openapi-ts config file
in lockstep with the kiln-side jsonnet view of the project.
``config/fe.jsonnet`` declares where the spec lives, where
output should land, and which plugins to enable; the fe target
translates that into ``openapi-ts.config.ts`` at the project
root.  ``yarn openapi-ts`` (wired into ``just generate``) then
runs the actual TS codegen against the emitted config.

Run with::

    foundry generate --target fe --config config/fe.jsonnet --out .

The ``yarn openapi-ts`` step that follows reads the emitted
config and produces ``src/_generated/`` (or whatever
``output_dir`` is set to).
"""
