"""ORM model for Author."""

from __future__ import annotations

from pgcraft.plugins.pk import UUIDV4PKPlugin
from sqlalchemy import Column, String, Text

from db.base import Base


class Author(Base):
    """Blog author with optional bio.

    The ``id`` column comes from :class:`UUIDV4PKPlugin` via
    ``__pgcraft__``; other fields are raw ``Column`` so pgcraft's
    ``_collect_columns`` picks them up.  ``mapped_column`` is
    intentionally not used -- pgcraft's plugin pipeline only
    recognises raw ``Column`` instances.
    """

    __tablename__ = "authors"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = [UUIDV4PKPlugin()]

    name = Column(String(120), nullable=False)
    email = Column(String(254), unique=True, nullable=False)
    bio = Column(Text, nullable=True)
