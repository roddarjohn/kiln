"""ORM models for the 'inventory' module.

All models use raw ``Column`` (not ``mapped_column``) so pgcraft's
plugin pipeline picks the fields up via ``_collect_columns``.
Primary keys are provided by pgcraft PK plugins declared in
``__pgcraft__``.
"""

from __future__ import annotations

from pgcraft import PGCraftForeignKey
from pgcraft.factory import PGCraftAppendOnly
from pgcraft.plugins.pk import UUIDV4PKPlugin
from sqlalchemy import Boolean, Column, Date, Float, Integer, String, Text

from db.base import Base


class Product(Base):
    """Stocked product with pricing and availability metadata."""

    __tablename__ = "products"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = [UUIDV4PKPlugin()]

    sku = Column(String(64), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    unit_price = Column(Float, nullable=False)
    stock_count = Column(Integer, default=0, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    available_from = Column(Date, nullable=True)


class StockMovement(Base):
    """Append-only record of stock quantity changes for a Product."""

    __tablename__ = "stock_movements"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = [UUIDV4PKPlugin()]

    product_id = Column(PGCraftForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    reason = Column(String(255), nullable=False)
    movement_date = Column(Date, nullable=False)


class EventLog(Base):
    """Append-only event log using SCD Type 2 semantics.

    Uses ``PGCraftAppendOnly`` so each write appends a new
    attributes row, preserving full history.  The ``event_logs``
    view always reflects the latest state of each entry.
    """

    __tablename__ = "event_logs"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = [PGCraftAppendOnly, UUIDV4PKPlugin()]

    event_type = Column(String(80), nullable=False)
    actor_email = Column(String(254), nullable=False)
    payload = Column(Text, nullable=True)
