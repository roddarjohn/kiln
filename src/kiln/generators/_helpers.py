"""Shared naming, import, and type-mapping helpers for code generators.

All mappings produce *strings* -- the textual representation of the
corresponding type in generated Python source code, not runtime
Python objects.
"""

from __future__ import annotations

from collections import defaultdict
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


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------


class Name:
    """Derives conventional identifiers from a base string.

    Accepts either a ``PascalCase`` class name (e.g. ``"Article"``)
    or a ``snake_case`` identifier (e.g. ``"publish_article"``) and
    exposes the common derived forms used by code generators.

    Examples::

        model = Name("Article")
        model.pascal              # "Article"
        model.lower               # "article"
        model.suffixed("Resource")  # "ArticleResource"

        action = Name("publish_article")
        action.pascal             # "PublishArticle"
        action.slug               # "publish-article"
        action.suffixed("Request")  # "PublishArticleRequest"

    """

    def __init__(self, raw: str) -> None:
        self.raw = raw

    @property
    def pascal(self) -> str:
        """PascalCase form of the name."""
        return "".join(part.capitalize() for part in self.raw.split("_"))

    @property
    def lower(self) -> str:
        """Fully lowercased form (for file/module names)."""
        return self.raw.lower()

    @property
    def slug(self) -> str:
        """Hyphenated slug form (for URL segments)."""
        return self.raw.replace("_", "-")

    def suffixed(self, suffix: str) -> str:
        """PascalCase name with *suffix* appended.

        Args:
            suffix: Class-name suffix, e.g. ``"CreateRequest"``.

        Returns:
            Combined string, e.g. ``"ArticleCreateRequest"``.

        """
        return f"{self.pascal}{suffix}"

    @classmethod
    def from_dotted(cls, dotted_path: str) -> tuple[str, Name]:
        """Create a :class:`Name` from a dotted import path.

        Args:
            dotted_path: A fully-qualified class path such as
                ``"myapp.models.Article"``.

        Returns:
            A ``(module, Name)`` tuple, e.g.
            ``("myapp.models", Name("Article"))``.

        """
        module, class_name = split_dotted_class(dotted_path)
        return module, cls(class_name)


# ---------------------------------------------------------------------------
# ImportCollector
# ---------------------------------------------------------------------------


class ImportCollector:
    """Accumulates Python import statements, deduplicating by module.

    Bare imports (``import uuid``) and from-imports
    (``from datetime import date``) are tracked separately.
    Multiple ``add_from`` calls for the same module are merged into a
    single ``from module import a, b`` line.

    Examples::

        collector = ImportCollector()
        collector.add("uuid")
        collector.add_from("datetime", "datetime")
        collector.add_from("datetime", "date")
        collector.lines()
        # ["import uuid", "from datetime import datetime, date"]

    """

    def __init__(self) -> None:
        self._bare: dict[str, None] = {}
        self._from: dict[str, dict[str, None]] = defaultdict(dict)

    def add(self, module: str) -> None:
        """Register a bare ``import module`` statement.

        Args:
            module: Module name, e.g. ``"uuid"``.

        """
        self._bare[module] = None

    def add_from(self, module: str, *names: str) -> None:
        """Register ``from module import name1, name2, ...``.

        Multiple calls with the same *module* are merged.

        Args:
            module: Module to import from, e.g. ``"datetime"``.
            *names: Names to import, e.g. ``"datetime"``,
                ``"date"``.

        """
        for name in names:
            self._from[module][name] = None

    def lines(self) -> list[str]:
        """Return the collected import statements as strings.

        Returns:
            List of import lines (no trailing newlines), bare
            imports first, then from-imports, each in insertion
            order.

        """
        result = [f"import {m}" for m in self._bare]
        for mod, names in self._from.items():
            result.append(f"from {mod} import {', '.join(names)}")
        return result


# ---------------------------------------------------------------------------
# Dotted-path and import-path helpers
# ---------------------------------------------------------------------------


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
            f"Expected 'module.ClassName', "
            f"e.g. 'myapp.models.Article'."
        )
        raise ValueError(msg)
    module, _, class_name = dotted_path.rpartition(".")
    return module, class_name


def prefix_import(prefix: str, *parts: str) -> str:
    """Build a Python import path under *prefix* (which may be empty).

    Args:
        prefix: Optional package prefix, e.g. ``"_generated"``.
        *parts: Module name segments to join with ``.``.

    Returns:
        A ``.``-joined import path, with *prefix* prepended when
        non-empty.

    """
    if prefix:
        return ".".join([prefix, *parts])
    return ".".join(parts)


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
