"""Language-neutral import collection.

:class:`ImportCollector` accumulates ``(module, name)`` pairs
produced by build operations.  Formatting is language-specific:
each language declares a formatter under the
``foundry.import_formatters`` entry-point group, then
:func:`format_imports` / :meth:`ImportCollector.format` resolves
it by language identifier.

The collector stores data only; it has no knowledge of PEP 8,
TypeScript ``from`` clauses, Go ``import`` blocks, or any other
language convention.  Each target ships its own formatter in
the package that owns the language.
"""

from __future__ import annotations

import functools
import importlib.metadata
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Entry-point group under which packages register import
#: formatters.  Each entry point's *name* is the language
#: identifier (e.g. ``"python"``, ``"rust"``); the value resolves
#: to a ``Callable[[ImportCollector], str]``.  Foundry declares
#: its own Python formatter in this group; third-party packages
#: add or override formatters by declaring their own entries.
_ENTRY_POINT_GROUP = "foundry.import_formatters"


class ImportCollector:
    r"""Accumulates imports as ``(module, name)`` pairs.

    A bare import is ``(module, None)`` (e.g. Python
    ``import uuid``).  A from-import is ``(module, name)`` (e.g.
    Python ``from datetime import datetime``).  Multiple calls
    for the same module are merged; duplicates are deduplicated.

    Examples::

        collector = ImportCollector()
        collector.add("uuid")
        collector.add_from("datetime", "datetime", "date")
        collector.format("python")
        # "import uuid\nfrom datetime import date, datetime\n"

    Rendering is delegated to a language formatter looked up via
    the ``foundry.import_formatters`` entry-point group.

    """

    def __init__(self, *others: ImportCollector) -> None:
        """Build an empty collector, or seed it from *others*.

        Args:
            *others: Collectors whose imports are unioned into
                the new instance.

        """
        self._bare: dict[str, None] = {}
        self._from: dict[str, dict[str, None]] = defaultdict(dict)

        for other in others:
            self.update(other=other)

    def add(self, module: str) -> None:
        """Register a bare ``<module>`` import."""
        self._bare[module] = None

    def add_from(self, module: str, *names: str) -> None:
        """Register an import of *names* from *module*.

        Multiple calls with the same *module* are merged.
        """
        for name in names:
            self._from[module][name] = None

    def update(self, other: ImportCollector) -> None:
        """Merge imports from *other* into this collector."""
        for module in other._bare:
            self._bare[module] = None

        for module, names in other._from.items():
            for name in names:
                self._from[module][name] = None

    def __or__(self, other: ImportCollector) -> ImportCollector:
        """Return a new collector with imports from both operands."""
        return ImportCollector(self, other)

    @property
    def bare_modules(self) -> list[str]:
        """Bare-imported modules (e.g. ``["uuid"]``)."""
        return list(self._bare)

    @property
    def from_imports(self) -> dict[str, list[str]]:
        """``{module: [name, ...]}`` of from-imports, preserving order."""
        return {module: list(names) for module, names in self._from.items()}

    def format(self, language: str) -> str:
        """Render the imports as a string in *language*'s syntax.

        Raises:
            KeyError: No formatter registered for *language*.

        """
        return format_imports(collector=self, language=language)


def format_imports(collector: ImportCollector, language: str) -> str:
    """Render *collector* using the formatter registered for *language*.

    Empty *language* returns the empty string; callers that do
    not configure a language simply get no import block.
    """
    if not language:
        return ""
    return _get_formatter(language=language)(collector)


@functools.cache
def _get_formatter(language: str) -> Callable[[ImportCollector], str]:
    """Resolve *language*'s formatter from the entry-point group.

    Cached per language after the first successful lookup;
    failed lookups are not cached, so a later plugin install is
    picked up on retry.
    """
    available = list(importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP))
    for entry_point in available:
        if entry_point.name == language:
            return entry_point.load()

    registered = sorted(entry_point.name for entry_point in available)
    msg = (
        f"No import formatter registered for language {language!r}; "
        f"registered: {registered}"
    )
    raise KeyError(msg)
