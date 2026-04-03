"""Shared declarative base for all playground models.

All model classes must inherit from ``Base`` so that Alembic can detect
every table in one place via ``Base.metadata``.
"""

from __future__ import annotations

from pgcraft import PGCraftBase


class Base(PGCraftBase):
    """Project-level base for all pgcraft models and views."""
