"""fe_root: one-shot bootstrap for a fe-driven frontend project.

Mirror of :mod:`be_root` but for the frontend toolchain: yarn,
TypeScript, React, Vite, and the
`@roddarjohn/glaze <https://github.com/roddarjohn/glaze>`_
component library.  Run with::

    foundry generate --target fe_root --config bootstrap.jsonnet --out .

Every emitted file is ``if_exists="skip"`` so a re-bootstrap leaves
post-bootstrap edits alone -- pass ``--force`` / ``--force-paths``
to clobber.
"""
