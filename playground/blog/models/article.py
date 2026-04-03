"""ORM model for Article."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgcraft import PGCraftForeignKey
from sqlalchemy import Boolean, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    from blog.models.author import Author


class Article(Base):
    """Published article written by an Author."""

    __tablename__ = "articles"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    published: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        PGCraftForeignKey("authors.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        default=func.now(), nullable=False
    )

    author: Mapped[Author] = relationship("Author", back_populates="articles")
