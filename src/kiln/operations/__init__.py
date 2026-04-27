"""New-protocol operations for kiln code generation.

Each sibling module contains ``@operation``-decorated classes that
produce the typed build outputs defined in
:mod:`kiln.operations.types` from their ``build()`` method.  The
engine discovers and runs those ops; the assembler renders their
output through the registered renderers in
:mod:`kiln.operations.renderers`.
"""
