"""Shared Jinja2 environment for all kiln code generators."""

from __future__ import annotations

from pathlib import Path

import jinja2

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

#: Jinja2 environment used by all generators.
#:
#: ``trim_blocks`` and ``lstrip_blocks`` are enabled so that block
#: tags (``{% if %}``, ``{% for %}``, etc.) do not add extra blank
#: lines to the rendered output.
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATES_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
    autoescape=False,  # noqa: S701 — generating Python source, not HTML
)
