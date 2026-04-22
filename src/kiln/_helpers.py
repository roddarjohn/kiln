"""Kiln-specific type mappings."""

from __future__ import annotations

# Python type annotation strings for Pydantic schemas and route
# parameters.
PYTHON_TYPES: dict[str, str] = {
    "uuid": "uuid.UUID",
    "str": "str",
    "email": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "datetime": "datetime",
    "date": "date",
    "json": "dict[str, Any]",
}
