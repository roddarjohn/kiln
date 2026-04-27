"""Check blank lines around control structures.

Enforces a project rule that every ``if``, ``for``, ``async for``,
``while``, ``try``, ``with``, ``async with``, and ``match``
statement carries a blank line both before and after it -- unless
the statement is the first or last in its enclosing block.

Comments immediately preceding a control structure count as
attached documentation: the required blank line goes *before* the
comment, not between the comment and the statement.

Run via::

    uv run python scripts/check_control_blank_lines.py [path ...]

Defaults to ``src`` and ``tests`` if no paths are given.  Exits 1
when any violation is found.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from pathlib import Path

CONTROL_TYPES: tuple[type[ast.stmt], ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)


def _control_name(node: ast.stmt) -> str:
    return type(node).__name__.removeprefix("Async").lower()


def _iter_bodies(node: ast.AST) -> Iterator[list[ast.stmt]]:
    """Yield each list-of-statements attached to *node*."""
    for attr in ("body", "orelse", "finalbody"):
        value = getattr(node, attr, None)

        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], ast.stmt)
        ):
            yield value

    if isinstance(node, ast.Try):
        for handler in node.handlers:
            yield handler.body

    if isinstance(node, ast.Match):
        for case in node.cases:
            yield case.body


def _line_above_is_blank(lines: list[str], lineno: int) -> bool:
    """Whether the line above ``lineno`` is blank.

    Skips contiguous comment-only lines so that a comment attached
    to the control structure does not block the rule.
    """
    idx = lineno - 2

    while idx >= 0 and lines[idx].lstrip().startswith("#"):
        idx -= 1

    if idx < 0:
        return True
    return lines[idx].strip() == ""


def _line_below_is_blank(lines: list[str], end_lineno: int) -> bool:
    idx = end_lineno

    if idx >= len(lines):
        return True
    return lines[idx].strip() == ""


def _check_body(
    body: list[ast.stmt], lines: list[str], path: Path
) -> Iterator[str]:
    last = len(body) - 1

    for i, stmt in enumerate(body):
        if isinstance(stmt, CONTROL_TYPES):
            if i > 0 and not _line_above_is_blank(lines, stmt.lineno):
                yield (
                    f"{path}:{stmt.lineno}: missing blank line before "
                    f"`{_control_name(stmt)}`"
                )

            if (
                i < last
                and stmt.end_lineno is not None
                and not _line_below_is_blank(lines, stmt.end_lineno)
            ):
                yield (
                    f"{path}:{stmt.end_lineno}: missing blank line after "
                    f"`{_control_name(stmt)}`"
                )

        for sub_body in _iter_bodies(stmt):
            yield from _check_body(sub_body, lines, path)


def check_file(path: Path) -> list[str]:
    """Return blank-line violations for ``path``."""
    src = path.read_text()

    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: failed to parse: {exc}"]

    lines = src.splitlines()
    return list(_check_body(tree.body, lines, path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("src"), Path("tests")],
    )
    args = parser.parse_args()

    violations: list[str] = []

    for root in args.paths:
        for path in sorted(root.rglob("*.py")):
            violations.extend(check_file(path))

    for v in violations:
        print(v)

    if violations:
        print(f"\n{len(violations)} violation(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
