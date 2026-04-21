"""Renderers for output types.

:data:`registry` is the process-wide
:class:`~foundry.render.RenderRegistry` every framework-specific
renderer module registers into.  Importing a framework module
(e.g. :mod:`kiln.renderers.fastapi`) or an op module installs
its renderers as a side effect -- alembic-style self-registration.
"""

from __future__ import annotations

from foundry.render import RenderRegistry

registry = RenderRegistry()

__all__ = ["registry"]
