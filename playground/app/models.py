"""ORM models for the 'app' module.

Defines User and Post tables for the example playground config.
"""

from __future__ import annotations

import uuid

from pgcraft import PGCraftForeignKey
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class User(Base):
    """Registered user with hashed credentials."""

    __tablename__ = "users"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    posts: Mapped[list[Post]] = relationship("Post", back_populates="author")


class Post(Base):
    """Blog post authored by a User."""

    __tablename__ = "posts"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(
        PGCraftForeignKey("users.id"), nullable=False
    )

    author: Mapped[User] = relationship("User", back_populates="posts")
