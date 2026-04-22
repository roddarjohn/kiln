"""Built-in Python formatter for :class:`ImportCollector`.

Imports are rendered following :pep:`8` / isort conventions:

1. ``from __future__`` imports (required first by Python).
2. Standard-library imports (bare then from, sorted).
3. Third-party / local imports (bare then from, sorted).

Blank lines separate the groups.  Long ``from`` lines are
wrapped with parentheses to stay within 80 characters.

Registered under the ``"python"`` language identifier via the
``foundry.import_formatters`` entry-point group declared in
foundry's ``pyproject.toml``.  Override by declaring your own
entry point with the same language name.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from foundry.imports import ImportCollector

#: Maximum line length for generated import statements.
_MAX_LINE = 80

#: Top-level module names that belong to the standard library.
_STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)


def format_python(collector: ImportCollector) -> str:
    r"""Render *collector* as a PEP 8 / isort Python import block.

    Returns a single string ending with ``\n`` when non-empty,
    or the empty string when the collector has no imports.
    """
    import_lines = _python_lines(collector)

    if not import_lines:
        return ""

    return "\n".join(import_lines) + "\n"


def _python_lines(collector: ImportCollector) -> list[str]:
    """Return the collected imports as PEP 8 / isort-ordered lines."""
    future: list[str] = []
    stdlib: list[str] = []
    third: list[str] = []

    from_imports = collector.from_imports

    if "__future__" in from_imports:
        future.append(
            _format_from_import(
                module="__future__",
                names=sorted(from_imports["__future__"]),
            )
        )

    for module in sorted(collector.bare_modules):
        top_level = module.split(".")[0]
        bucket = stdlib if top_level in _STDLIB else third
        bucket.append(f"import {module}")

    for module in sorted(from_imports):
        if module == "__future__":
            continue
        line = _format_from_import(
            module=module,
            names=sorted(from_imports[module]),
        )
        top_level = module.split(".")[0]
        bucket = stdlib if top_level in _STDLIB else third
        bucket.append(line)

    groups = [group for group in (future, stdlib, third) if group]
    result: list[str] = []

    for index, group in enumerate(groups):
        if index > 0:
            result.append("")
        result.extend(group)

    return result


def _format_from_import(module: str, names: list[str]) -> str:
    """Format a ``from module import ...`` statement.

    Single-line when it fits within :data:`_MAX_LINE`; wrapped
    with parentheses otherwise.
    """
    single = f"from {module} import {', '.join(names)}"

    if len(single) <= _MAX_LINE:
        return single

    joined = ",\n    ".join(names)

    return f"from {module} import (\n    {joined},\n)"
