"""Renderers for output types.

Each renderer turns a build output into a code string using
Jinja2 templates.  The :data:`registry` is the default
:class:`~foundry.render.RenderRegistry` pre-loaded with
all FastAPI renderers.
"""
