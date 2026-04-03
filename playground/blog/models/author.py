"""ORM model for Author."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    from blog.models.article import Article


class Author(Base):
    """Blog author with optional bio."""

    __tablename__ = "authors"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    articles: Mapped[list[Article]] = relationship(
        "Article", back_populates="author"
    )
