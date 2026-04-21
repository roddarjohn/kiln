"""Configuration models for the ``list`` operation's extensions.

These are the three optional sub-configs that turn a plain list
endpoint into a filterable / orderable / paginated one.  The
:class:`~kiln.operations.list.List` operation reads them from its
``Options`` model and translates them into
:class:`~foundry.outputs.SchemaClass` and
:class:`~foundry.outputs.RouteHandler` output.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from kiln.config.schema import FieldType  # noqa: TC001


class FilterConfig(BaseModel):
    """Configuration for list filtering.

    When ``fields`` is omitted or empty, all fields from the list
    operation's ``fields`` config become filterable.  Otherwise
    only the named fields are filterable.
    """

    fields: list[str] | None = None


class OrderConfig(BaseModel):
    """Configuration for list ordering."""

    fields: list[str]
    default: str | None = None
    default_dir: Literal["asc", "desc"] = "asc"


class PaginateConfig(BaseModel):
    """Configuration for list pagination."""

    mode: Literal["keyset", "offset"] = "keyset"
    cursor_field: str = "id"
    cursor_type: FieldType = "uuid"
    max_page_size: int = 100
    default_page_size: int = 20
