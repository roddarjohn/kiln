"""ORM models for the 'inventory' module.

All models use raw ``Column`` (not ``mapped_column``) so pgcraft's
plugin pipeline picks the fields up via ``_collect_columns``.
Primary keys are provided by pgcraft PK plugins declared in
``__pgcraft__``.

The ``Customer`` and ``SavedView`` models exercise the link /
``ref`` filter / saved-view surface added in the filtering plan.
"""

from __future__ import annotations

from ingot.saved_views import SavedViewMixin
from pgcraft import PGCraftForeignKey
from pgcraft.factory import PGCraftAppendOnly
from pgcraft.plugins.pk import UUIDV4PKPlugin
from sqlalchemy import Boolean, Column, Date, Float, Integer, String, Text

from db.base import Base


class Customer(Base):
    """A customer that places orders / owns Products in the catalog."""

    __tablename__ = "customers"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = [UUIDV4PKPlugin()]

    name = Column(String(255), nullable=False)
    email = Column(String(254), nullable=False, unique=True)


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
    customer_id = Column(
        PGCraftForeignKey("customers.id"), nullable=True
    )


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


class SavedView(Base, SavedViewMixin):
    """Per-user saved view — columns supplied by SavedViewMixin.

    The mixin contributes ``resource_type``, ``owner_id``,
    ``name``, ``payload``, ``created_at``, ``updated_at``.  The
    ``id`` PK comes from the ``UUIDV4PKPlugin`` below.
    """

    __tablename__ = "saved_views"
    __table_args__ = {"schema": "public"}
    __pgcraft__ = [UUIDV4PKPlugin()]
