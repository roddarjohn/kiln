"""Shared type-mapping helpers used across code generators.

All mappings produce *strings* — the textual representation of the
corresponding type in generated Python source code, not runtime
Python objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiln.config.schema import DatabaseConfig, FieldConfig, FieldType

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

# Default PK plugin dotted paths used when primary_key=True.
# Keyed by FieldType; users can override with a dotted path string.
_DEFAULT_PK_PLUGINS: dict[str, str] = {
    "uuid": "pgcraft.plugins.pk.UUIDV4PKPlugin",
    "int": "pgcraft.plugins.pk.SerialPKPlugin",
}


def split_dotted_class(dotted_path: str) -> tuple[str, str]:
    """Split a dotted import path into ``(module, class_name)``.

    Args:
        dotted_path: A fully-qualified class path such as
            ``"pgcraft.factory.dimension.simple.PGCraftSimple"``.

    Returns:
        A ``(module, class_name)`` tuple, e.g.
        ``("pgcraft.factory.dimension.simple", "PGCraftSimple")``.

    Raises:
        ValueError: If *dotted_path* contains fewer than two parts.

    """
    if "." not in dotted_path:
        msg = (
            f"'{dotted_path}' is not a valid dotted import path. "
            f"Expected 'module.ClassName', e.g. "
            f"'pgcraft.factory.dimension.simple.PGCraftSimple'."
        )
        raise ValueError(msg)
    module, _, class_name = dotted_path.rpartition(".")
    return module, class_name


def resolve_pk_plugin(field_type: str, primary_key: bool | str) -> str:  # noqa: FBT001
    """Return the dotted import path for the PK plugin.

    Args:
        field_type: The :data:`FieldType` of the primary-key field.
        primary_key: ``True`` to use the default plugin for
            *field_type*, or a dotted import path string to use that
            specific plugin class.

    Returns:
        Dotted import path to the PK plugin class.

    Raises:
        ValueError: When *primary_key* is ``True`` but *field_type*
            has no default PK plugin registered.

    """
    if isinstance(primary_key, str):
        return primary_key
    if field_type not in _DEFAULT_PK_PLUGINS:
        msg = (
            f"No default PK plugin for field type '{field_type}'. "
            f"Pass a dotted import path as primary_key, e.g. "
            f"primary_key='pgcraft.plugins.pk.SerialPKPlugin'."
        )
        raise ValueError(msg)
    return _DEFAULT_PK_PLUGINS[field_type]


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


def resolve_db_session(
    db_key: str | None,
    databases: list[DatabaseConfig],
) -> tuple[str, str]:
    """Return the ``(session_module, get_db_fn)`` pair for *db_key*.

    When no databases are configured the legacy single-database session
    layout is assumed (``db.session`` / ``get_db``).  With databases
    configured, the default database is used when *db_key* is ``None``.

    Args:
        db_key: The ``db_key`` value from a model or view config.
        databases: The project-level database list from ``KilnConfig``.

    Returns:
        A ``(session_module, get_db_fn)`` tuple suitable for template
        rendering, e.g. ``("db.primary_session", "get_primary_db")``.

    Raises:
        ValueError: When *db_key* does not match any configured database,
            or when no database has ``default=True`` and *db_key* is ``None``.

    """
    if not databases:
        return ("db.session", "get_db")
    if db_key is None:
        defaults = [d for d in databases if d.default]
        if not defaults:
            msg = (
                "No database has default=True. "
                "Set default: true on one database or specify db_key."
            )
            raise ValueError(msg)
        db = defaults[0]
    else:
        matches = [d for d in databases if d.key == db_key]
        if not matches:
            msg = f"No database with key '{db_key}' found in databases config."
            raise ValueError(msg)
        db = matches[0]
    return (f"db.{db.key}_session", f"get_{db.key}_db")


def sa_type(field_type: FieldType) -> str:
    """Return the SQLAlchemy column type string for *field_type*."""
    return SA_TYPES[field_type]


def python_type(field_type: FieldType) -> str:
    """Return the Python type annotation string for *field_type*."""
    return PYTHON_TYPES[field_type]


def pg_sql_type(field_type: FieldType) -> str:
    """Return the PostgreSQL SQL type string for *field_type*."""
    return PG_SQL_TYPES[field_type]


def _to_pgcraft_fk_ref(foreign_key: str) -> str:
    """Convert a FK reference string to pgcraft's two-part dimension format.

    pgcraft's dimension registry uses ``"table.column"`` references.
    Config values may be fully qualified (``"schema.table.column"``), in
    which case the schema prefix is stripped so that pgcraft can resolve
    the dimension to the correct underlying ``_raw`` table via the registry.

    Args:
        foreign_key: The ``foreign_key`` string from ``FieldConfig``.

    Returns:
        A two-part ``"dimension.column"`` string suitable for
        ``PGCraftForeignKey``.

    """
    parts = foreign_key.split(".")
    if len(parts) == 3:  # noqa: PLR2004
        # schema.table.column → table.column (dimension ref)
        return f"{parts[1]}.{parts[2]}"
    return foreign_key


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
        fk_ref = _to_pgcraft_fk_ref(field.foreign_key)
        args.append(f'PGCraftForeignKey("{fk_ref}")')
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
