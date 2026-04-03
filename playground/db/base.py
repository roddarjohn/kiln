"""Shared SQLAlchemy declarative base for all playground models.

All model classes must inherit from ``Base`` so that Alembic can detect
every table in one place via ``Base.metadata``.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-level declarative base for all playground ORM models."""
