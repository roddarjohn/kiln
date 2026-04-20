"""Kiln-specific type mappings and database helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiln.config.schema import DatabaseConfig

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


def resolve_db_session(
    db_key: str | None,
    databases: list[DatabaseConfig],
) -> tuple[str, str]:
    """Return the ``(session_module, get_db_fn)`` pair for *db_key*.

    When no databases are configured the legacy single-database
    session layout is assumed (``db.session`` / ``get_db``).  With
    databases configured, the default database is used when *db_key*
    is ``None``.

    Args:
        db_key: The ``db_key`` value from a resource config.
        databases: The project-level database list from
            ``KilnConfig``.

    Returns:
        A ``(session_module, get_db_fn)`` tuple suitable for
        template rendering, e.g.
        ``("db.primary_session", "get_primary_db")``.

    Raises:
        ValueError: When *db_key* does not match any configured
            database, or when no database has ``default=True`` and
            *db_key* is ``None``.

    """
    if not databases:
        return ("db.session", "get_db")
    if db_key is None:
        defaults = [d for d in databases if d.default]
        if not defaults:
            msg = (
                "No database has default=True. "
                "Set default: true on one database "
                "or specify db_key."
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
