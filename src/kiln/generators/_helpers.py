"""Shared type-mapping helpers used across code generators.

All mappings produce *strings* — the textual representation of the
corresponding type in generated Python source code, not runtime
Python objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiln.config.schema import DatabaseConfig

# Python type annotation strings for Pydantic schemas and route parameters.
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


def split_dotted_class(dotted_path: str) -> tuple[str, str]:
    """Split a dotted import path into ``(module, class_name)``.

    Args:
        dotted_path: A fully-qualified class path such as
            ``"myapp.models.Article"``.

    Returns:
        A ``(module, class_name)`` tuple, e.g.
        ``("myapp.models", "Article")``.

    Raises:
        ValueError: If *dotted_path* contains fewer than two parts.

    """
    if "." not in dotted_path:
        msg = (
            f"'{dotted_path}' is not a valid dotted import path. "
            f"Expected 'module.ClassName', e.g. 'myapp.models.Article'."
        )
        raise ValueError(msg)
    module, _, class_name = dotted_path.rpartition(".")
    return module, class_name


def type_imports(field_types: list[str]) -> list[str]:
    """Return the import lines needed for the given field type names.

    Produces a minimal, ordered list of ``import`` / ``from ... import``
    statements covering ``uuid``, ``datetime``/``date``, and ``Any`` as
    required.  Pass the result directly to templates as ``imports`` and
    render with ``{% for imp in imports %}{{ imp }}{% endfor %}``.

    Args:
        field_types: List of :data:`FieldType` strings, e.g.
            ``[f.type for f in fields]``.

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
        db_key: The ``db_key`` value from a resource config.
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
