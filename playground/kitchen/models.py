"""ORM models for the 'kitchen' module.

Defines User, Category, AuditLog, Product, and Order tables for the
kitchen-sink playground config, which exercises every kiln feature.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgcraft import PGCraftForeignKey
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class User(Base):
    """Application user."""

    __tablename__ = "kitchen_users"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        nullable=False,
    )

    orders: Mapped[list[Order]] = relationship("Order", back_populates="user")


class Category(Base):
    """Product category with an integer primary key."""

    __tablename__ = "categories"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


class AuditLog(Base):
    """Append-only record of user actions in the system."""

    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)


class Product(Base):
    """Kitchen-sink product with all supported field types."""

    __tablename__ = "kitchen_products"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    stock_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)

    orders: Mapped[list[Order]] = relationship("Order", back_populates="product")


class Order(Base):
    """Customer order linking a User to a Product."""

    __tablename__ = "orders"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGCraftForeignKey("kitchen_users.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGCraftForeignKey("kitchen_products.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="orders")
    product: Mapped[Product] = relationship("Product", back_populates="orders")
