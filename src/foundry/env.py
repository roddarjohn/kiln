"""Jinja2 environment creation and snippet rendering.

Provides a factory for creating Jinja2 environments configured
for code generation, and a helper for rendering template
snippets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import jinja2

if TYPE_CHECKING:
    from pathlib import Path


def create_jinja_env(
    *template_dirs: Path,
) -> jinja2.Environment:
    """Create a Jinja2 environment for code generation.

    The returned environment has ``trim_blocks`` and
    ``lstrip_blocks`` enabled so that block tags
    (``{% if %}``, ``{% for %}``, etc.) do not add extra blank
    lines to the rendered output.

    Args:
        *template_dirs: One or more directories to search for
            templates.  Earlier directories take priority.

    Returns:
        A configured :class:`jinja2.Environment`.

    """
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(
            [str(template_dir) for template_dir in template_dirs],
        ),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
        autoescape=False,  # noqa: S701 — generating source, not HTML
    )


def render_snippet(
    env: jinja2.Environment,
    template_name: str,
    **ctx: object,
) -> str:
    """Render a template snippet and return it as a string.

    Useful for rendering per-operation fragments (route handlers,
    schema classes) that are later assembled into an outer file
    template.

    Args:
        env: The Jinja2 environment to use.
        template_name: Template path relative to the template
            directory, e.g. ``"fastapi/ops/get.py.j2"``.
        **ctx: Template context variables.

    Returns:
        Rendered template string with leading/trailing whitespace
        stripped.

    """
    return env.get_template(template_name).render(**ctx).strip()
