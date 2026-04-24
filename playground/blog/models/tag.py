"""ORM model for Tag."""

from __future__ import annotations

from sqlalchemy import Column, String

from db.base import Base


class Tag(Base):
    """Content tag with an integer primary key.

    ``id`` is added automatically by pgcraft's default
    :class:`SerialPKPlugin` (Integer PK).  Other fields are raw
    ``Column`` so pgcraft's ``_collect_columns`` picks them up.
    """

    __tablename__ = "tags"
    __table_args__ = {"schema": "public"}

    name = Column(String(80), unique=True, nullable=False)
    slug = Column(String(80), unique=True, nullable=False)
