"""Import statement collection and deduplication.

Provides :class:`ImportCollector`, which accumulates Python
import statements and renders them as a correctly grouped,
deduplicated block following :pep:`8` / isort conventions.
"""

from __future__ import annotations

import sys
from collections import defaultdict

#: Maximum line length for generated import statements.
_MAX_LINE = 80

#: Top-level module names that belong to the standard library.
_STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)


class ImportCollector:
    """Accumulates Python import statements, deduplicating by module.

    Bare imports (``import uuid``) and from-imports
    (``from datetime import date``) are tracked separately.
    Multiple :meth:`add_from` calls for the same module are merged
    into a single ``from module import a, b`` line.

    Examples::

        collector = ImportCollector()
        collector.add("uuid")
        collector.add_from("datetime", "datetime")
        collector.add_from("datetime", "date")
        collector.lines()
        # ["import uuid", "from datetime import datetime, date"]

    """

    def __init__(self) -> None:  # noqa: D107
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

    def update(self, other: ImportCollector) -> None:
        """Merge imports from *other* into this collector.

        Bare imports and from-imports are both unioned; duplicates
        are deduplicated.  Used when multiple fragments targeting
        the same output file need their import sets combined.

        Args:
            other: Another :class:`ImportCollector` to merge in.

        """
        for module_name in other._bare:
            self._bare[module_name] = None
        for module_name, names in other._from.items():
            for name in names:
                self._from[module_name][name] = None

    def block(self) -> str:
        """Return all imports as a single string block.

        Convenience wrapper around :meth:`lines` -- joins
        lines with newlines and appends a trailing newline
        when non-empty.

        Returns:
            Import block string, or empty string when no
            imports have been collected.

        """
        import_lines = self.lines()
        if not import_lines:
            return ""
        return "\n".join(import_lines) + "\n"

    def lines(self) -> list[str]:
        """Return the collected import statements as strings.

        Imports are grouped and sorted following :pep:`8` /
        isort conventions:

        1. ``from __future__`` imports (required first by Python).
        2. Standard-library imports (bare then from, sorted).
        3. Third-party / local imports (bare then from, sorted).

        Blank lines separate the groups.  Long ``from`` lines are
        wrapped with parentheses to stay within 80 characters.

        Returns:
            List of import lines (no trailing newlines).

        """
        future: list[str] = []
        stdlib: list[str] = []
        third: list[str] = []

        # --- from __future__ ---
        if "__future__" in self._from:
            names = sorted(self._from["__future__"])
            future.append(
                _format_from_import("__future__", names),
            )

        # --- bare imports ---
        for module_name in sorted(self._bare):
            top_level = module_name.split(".")[0]
            bucket = stdlib if top_level in _STDLIB else third
            bucket.append(f"import {module_name}")

        # --- from imports ---
        for module_name in sorted(self._from):
            if module_name == "__future__":
                continue
            names = sorted(self._from[module_name])
            line = _format_from_import(module_name, names)
            top_level = module_name.split(".")[0]
            bucket = stdlib if top_level in _STDLIB else third
            bucket.append(line)

        # Assemble groups with blank-line separators.
        groups = [grp for grp in (future, stdlib, third) if grp]
        result: list[str] = []
        for index, group in enumerate(groups):
            if index > 0:
                result.append("")
            result.extend(group)
        return result


def _format_from_import(module: str, names: list[str]) -> str:
    """Format a ``from module import ...`` statement.

    If the single-line form fits within :data:`_MAX_LINE`, it is
    returned as-is.  Otherwise the names are wrapped across
    multiple lines using parentheses.

    Args:
        module: Module to import from.
        names: Sorted list of names to import.

    Returns:
        One or more lines of Python import code.

    """
    single = f"from {module} import {', '.join(names)}"
    if len(single) <= _MAX_LINE:
        return single
    joined = ",\n    ".join(names)
    return f"from {module} import (\n    {joined},\n)"
