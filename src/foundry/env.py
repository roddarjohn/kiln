"""Jinja2 environment creation and template rendering."""

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


def render_template(
    env: jinja2.Environment,
    template_name: str,
    **context: object,
) -> str:
    r"""Render *template_name* against *context* and return the raw result.

    Every jinja call in foundry/kiln flows through this helper so
    whitespace policy lives at the call site, not hidden inside a
    render wrapper.  Callers handle trimming themselves:

    * Inline code snippets typically want ``.strip()``.
    * Whole-file output wants ``.rstrip() + "\n"``.
    * Slot contributions that the outer template controls
      typically want the raw output, unmodified.

    Args:
        env: The Jinja2 environment to use.
        template_name: Template path relative to the environment's
            template directories.
        **context: Template context variables.

    Returns:
        The rendered template, exactly as jinja produced it.

    """
    return env.get_template(template_name).render(**context)
