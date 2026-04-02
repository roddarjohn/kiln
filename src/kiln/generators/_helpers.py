"""Shared type-mapping helpers used across code generators.

All mappings produce *strings* — the textual representation of the
corresponding type in generated Python source code, not runtime
Python objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiln.config.schema import FieldConfig, FieldType

# SQLAlchemy column type constructor strings.
# Values are written verbatim into generated Column(...) calls.
SA_TYPES: dict[str, str] = {
    "uuid": "pg.UUID(as_uuid=True)",
    "str": "String",
    "email": "String",
    "int": "Integer",
    "float": "Float",
    "bool": "Boolean",
    "datetime": "pg.TIMESTAMP(timezone=True)",
    "date": "pg.DATE",
    "json": "pg.JSONB",
}

# Python type annotation strings for Pydantic schemas.
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

# PostgreSQL SQL type names for function RETURNS TABLE(...) clauses.
PG_SQL_TYPES: dict[str, str] = {
    "uuid": "UUID",
    "str": "TEXT",
    "email": "TEXT",
    "int": "INTEGER",
    "float": "DOUBLE PRECISION",
    "bool": "BOOLEAN",
    "datetime": "TIMESTAMPTZ",
    "date": "DATE",
    "json": "JSONB",
}

# SQLAlchemy column type *instance* strings for table_valued() calls.
SA_INSTANCE_TYPES: dict[str, str] = {
    "uuid": "pg.UUID()",
    "str": "String()",
    "email": "String()",
    "int": "Integer()",
    "float": "Float()",
    "bool": "Boolean()",
    "datetime": "pg.TIMESTAMP(timezone=True)",
    "date": "pg.DATE()",
    "json": "pg.JSONB()",
}

# (factory_class_name, import_module) for each pgcraft_type value.
PGCRAFT_FACTORIES: dict[str, tuple[str, str]] = {
    "simple": (
        "PGCraftSimple",
        "pgcraft.factory.dimension.simple",
    ),
    "append_only": (
        "PGCraftAppendOnly",
        "pgcraft.factory.dimension.append_only",
    ),
    "ledger": (
        "PGCraftLedger",
        "pgcraft.factory.ledger",
    ),
    "eav": (
        "PGCraftEAV",
        "pgcraft.factory.dimension.eav",
    ),
}


def type_imports(field_types: list[str]) -> list[str]:
    """Return the import lines needed for the given field type names.

    Produces a minimal, ordered list of ``import`` / ``from ... import``
    statements covering ``uuid``, ``datetime``/``date``, and ``Any`` as
    required.  Pass the result directly to templates as ``imports`` and
    render with ``{% for imp in imports %}{{ imp }}{% endfor %}``.

    Args:
        field_types: List of :data:`FieldType` strings, e.g.
            ``[f.type for f in model.fields]``.

    Returns:
        List of import statement strings (no trailing newlines).

    """
    types = set(field_types)
    lines: list[str] = []
    if "uuid" in types:
        lines.append("import uuid")
    dt_parts = [t for t in ("datetime", "date") if t in types]
    if dt_parts:
        lines.append(f"from datetime import {', '.join(dt_parts)}")
    if "json" in types:
        lines.append("from typing import Any")
    return lines


def sa_type(field_type: FieldType) -> str:
    """Return the SQLAlchemy column type string for *field_type*."""
    return SA_TYPES[field_type]


def python_type(field_type: FieldType) -> str:
    """Return the Python type annotation string for *field_type*."""
    return PYTHON_TYPES[field_type]


def pg_sql_type(field_type: FieldType) -> str:
    """Return the PostgreSQL SQL type string for *field_type*."""
    return PG_SQL_TYPES[field_type]


def column_def(field: FieldConfig) -> str:
    """Return the ``Column(...)`` constructor call string for *field*.

    Produces a string suitable for direct inclusion in generated
    SQLAlchemy model source code, e.g.::

        Column(pg.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    Args:
        field: The field configuration to render.

    Returns:
        A ``Column(...)`` expression as a string.

    """
    args: list[str] = [SA_TYPES[field.type]]
    if field.foreign_key:
        args.append(f'PGCraftForeignKey("{field.foreign_key}")')
    if field.primary_key:
        args.append("primary_key=True")
        if field.type == "uuid":
            args.append("default=uuid.uuid4")
    if field.unique:
        args.append("unique=True")
    if field.nullable:
        args.append("nullable=True")
    elif not field.primary_key:
        args.append("nullable=False")
    if field.auto_now_add:
        args.append("server_default=func.now()")
    if field.auto_now:
        args.append("onupdate=func.now()")
    if field.index:
        args.append("index=True")
    return f"Column({', '.join(args)})"
