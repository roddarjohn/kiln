"""Shared Jinja2 environment for all kiln code generators.

Creates a module-level :data:`env` using kiln's bundled template
directory and provides a convenience :func:`render_snippet` that
delegates to :func:`foundry.env.render_snippet` with the
pre-configured environment.
"""

from __future__ import annotations

from pathlib import Path

from foundry.env import create_jinja_env
from foundry.env import render_snippet as _core_render_snippet

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

#: Jinja2 environment used by all kiln generators.
#:
#: ``trim_blocks`` and ``lstrip_blocks`` are enabled so that block
#: tags (``{% if %}``, ``{% for %}``, etc.) do not add extra blank
#: lines to the rendered output.
env = create_jinja_env(_TEMPLATES_DIR)


def render_snippet(template_name: str, **ctx: object) -> str:
    """Render a template snippet and return it as a string.

    Used by :class:`~kiln.generators.fastapi.operations.Operation`
    classes to render per-operation handler and schema fragments
    that are then assembled into the outer file template.

    Args:
        template_name: Template path relative to the templates
            directory, e.g. ``"fastapi/ops/get.py.j2"``.
        **ctx: Template context variables.

    Returns:
        Rendered template string.

    """
    return _core_render_snippet(env, template_name, **ctx)
