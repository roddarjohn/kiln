"""ORM model for Article."""

from __future__ import annotations

from pgcraft import PGCraftForeignKey
from pgcraft.plugins.pk import UUIDV4PKPlugin
from sqlalchemy import Boolean, Column, DateTime, String, Text, func

from db.base import Base


class Article(Base):
    """Published article written by an Author.

    The ``id`` column comes from :class:`UUIDV4PKPlugin` via
    ``__pgcraft__``; other fields are raw ``Column`` so pgcraft's
    ``_collect_columns`` picks them up.
    """

    __tablename__ = "articles"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = [UUIDV4PKPlugin()]

    title = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False)
    body = Column(Text, nullable=False)
    published = Column(Boolean, default=False, nullable=False)
    author_id = Column(PGCraftForeignKey("authors.id"), nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
