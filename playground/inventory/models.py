"""ORM models for the 'inventory' module."""

from __future__ import annotations

import uuid
from datetime import date

from pgcraft import PGCraftForeignKey
from pgcraft.factory import PGCraftAppendOnly
from sqlalchemy import Boolean, Column, Date, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class Product(Base):
    """Stocked product with pricing and availability metadata."""

    __tablename__ = "products"
    __table_args__ = {"schema": "public"}

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
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGCraftForeignKey("products.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)

    product: Mapped[Product] = relationship("Product", back_populates="movements")


class EventLog(Base):
    """Append-only event log using SCD Type 2 semantics.

    Uses ``PGCraftAppendOnly`` so each write appends a new attributes
    row, preserving full history.  The ``event_logs`` view always
    reflects the latest state of each entry.

    Raw ``Column`` instances (not ``mapped_column``) are required here
    so that pgcraft's plugin pipeline can collect and register them.
    """

    __tablename__ = "event_logs"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = {"factory": PGCraftAppendOnly}

    event_type = Column(String(80), nullable=False)
    actor_email = Column(String(254), nullable=False)
    payload = Column(Text, nullable=True)
