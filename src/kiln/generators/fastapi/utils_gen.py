"""Shared utility module for generated FastAPI routes."""

from __future__ import annotations

from kiln.generators._env import env
from kiln_core import GeneratedFile


def generate_utils() -> list[GeneratedFile]:
    """Generate ``utils.py`` with shared route helpers.

    Returns:
        A single :class:`GeneratedFile`.

    """
    tmpl = env.get_template("fastapi/utils.py.j2")
    return [GeneratedFile(path="utils.py", content=tmpl.render())]
