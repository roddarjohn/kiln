"""SQLAlchemy ORM models for the 'inventory' module.

Defines Product and StockMovement tables for the inventory playground config.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    pass


class Product(Base):
    """Stocked product with pricing and availability metadata."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    stock_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)

    movements: Mapped[list[StockMovement]] = relationship(
        "StockMovement", back_populates="product"
    )


class StockMovement(Base):
    """Append-only record of stock quantity changes for a Product."""

    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)

    product: Mapped[Product] = relationship("Product", back_populates="movements")
