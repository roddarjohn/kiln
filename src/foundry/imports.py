"""Language-neutral import collection.

:class:`ImportCollector` accumulates ``(module, name)`` pairs
produced by build operations.  Formatting is language-specific:
callers register a formatter via :func:`register_formatter` for
their target language (e.g. ``"python"``), then request output
via :meth:`ImportCollector.format` or :func:`format_imports`
with a language selector.

The collector stores data only; it has no knowledge of PEP 8,
TypeScript ``from`` clauses, Go ``import`` blocks, or any other
language convention.  Each target ships its own formatter in
the package that owns the language.
"""

from __future__ import annotations

import importlib.metadata
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Entry-point group under which targets may register import
#: formatters.  Each entry point's *name* is the language
#: identifier (e.g. ``"python"``, ``"rust"``); the value resolves
#: to a ``Callable[[ImportCollector], str]``.  Built-in
#: formatters register at module load time and are overridden by
#: any entry-point with the same name.
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

    Rendering is delegated to a registered formatter — see
    :func:`register_formatter`.

    """

    def __init__(self) -> None:  # noqa: D107
        self._bare: dict[str, None] = {}
        self._from: dict[str, dict[str, None]] = defaultdict(dict)

    def add(self, module: str) -> None:
        """Register a bare ``<module>`` import.

        Args:
            module: Module name, e.g. ``"uuid"``.

        """
        self._bare[module] = None

    def add_from(self, module: str, *names: str) -> None:
        """Register an import of *names* from *module*.

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
        are deduplicated.
        """
        for module in other._bare:
            self._bare[module] = None
        for module, names in other._from.items():
            for name in names:
                self._from[module][name] = None

    def __or__(self, other: ImportCollector) -> ImportCollector:
        """Return a new collector with imports from both operands."""
        combined = ImportCollector()
        combined.update(other=self)
        combined.update(other=other)
        return combined

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

        Args:
            language: Identifier of a registered formatter,
                e.g. ``"python"``.

        Returns:
            The formatted import block (may be empty).

        Raises:
            KeyError: No formatter registered for *language*.

        """
        return format_imports(collector=self, language=language)


#: Language → formatter.  Populated by :func:`register_formatter`,
#: which built-in registration and entry-point discovery both
#: call at module load.  Third-party plugins can override
#: built-ins by calling :func:`register_formatter` (or declaring
#: an entry point) with the same language name.
_FORMATTERS: dict[str, Callable[[ImportCollector], str]] = {}


def register_formatter(
    language: str,
    formatter: Callable[[ImportCollector], str],
) -> None:
    """Register an import-block formatter for *language*.

    Targets call this at import time so the assembler can render
    imports for their language.

    Args:
        language: Language identifier, e.g. ``"python"``.
        formatter: Callable that turns a collector into a
            language-appropriate import block string.

    """
    _FORMATTERS[language] = formatter


def format_imports(collector: ImportCollector, language: str) -> str:
    """Render *collector* using the formatter registered for *language*.

    Empty *language* returns the empty string; callers that do
    not configure a language simply get no import block.
    """
    if not language:
        return ""

    formatter = _FORMATTERS.get(language)

    if formatter is None:
        registered = sorted(_FORMATTERS)
        msg = (
            f"No import formatter registered for language {language!r}; "
            f"registered: {registered}"
        )
        raise KeyError(msg)

    return formatter(collector)


def _register_entry_point_formatters() -> None:
    """Load ``foundry.import_formatters`` entry points.

    Loaded after built-ins so third-party plugins can override
    the Python formatter (or any other built-in) by declaring an
    entry point with the same language name.
    """
    entry_points = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
    for entry_point in entry_points:
        register_formatter(
            language=entry_point.name,
            formatter=entry_point.load(),
        )


# Built-ins register eagerly; entry points run after so that
# plugins declaring "python" (or any other name) override the
# ones foundry ships with.
from foundry._python_imports import format_python  # noqa: E402

register_formatter(language="python", formatter=format_python)
_register_entry_point_formatters()
