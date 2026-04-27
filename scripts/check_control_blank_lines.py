"""Check (and optionally fix) blank lines around control structures.

Enforces a project rule that:

1. Every ``if``, ``for``, ``async for``, ``while``, ``try``, ``with``,
   ``async with``, and ``match`` statement carries a blank line both
   before and after it -- unless it is first or last in its enclosing
   block.
2. Every continuation clause (``elif``, ``else``, ``except``,
   ``finally``) carries a blank line before its keyword, separating
   it from the preceding branch's body.

Comments immediately preceding a top-level control structure count
as attached documentation: the required blank line goes *before*
the comment, not between the comment and the statement.

Run via::

    uv run python scripts/check_control_blank_lines.py
    uv run python scripts/check_control_blank_lines.py --fix
    uv run python scripts/check_control_blank_lines.py src/foundry

Defaults to ``src`` and ``tests`` if no paths are given.  Without
``--fix`` exits 1 when any violation is found; with ``--fix``
exits 0 after writing the inserts back to disk.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    """Yield each list-of-statements attached to ``node``."""
    for attr in ("body", "orelse", "finalbody"):
        value = getattr(node, attr, None)

        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            yield value

    if isinstance(node, ast.Try):
        for handler in node.handlers:
            yield handler.body

    if isinstance(node, ast.Match):
        for case in node.cases:
            yield case.body


def _branch_gaps(stmt: ast.stmt) -> Iterator[tuple[str, int]]:
    """Yield ``(clause_keyword, prev_end_lineno)`` per continuation.

    ``prev_end_lineno`` is the 1-indexed last line of the branch
    *before* the continuation -- the line index right after it (0-
    indexed) is where a blank line must live to satisfy the rule.
    """
    if isinstance(stmt, ast.If) and stmt.orelse:
        is_elif = (
            len(stmt.orelse) == 1
            and isinstance(stmt.orelse[0], ast.If)
            and stmt.orelse[0].col_offset == stmt.col_offset
        )
        kw = "elif" if is_elif else "else"
        end = stmt.body[-1].end_lineno

        if end is not None:
            yield (kw, end)

    elif isinstance(stmt, ast.For | ast.AsyncFor | ast.While) and stmt.orelse:
        end = stmt.body[-1].end_lineno

        if end is not None:
            yield ("else", end)

    elif isinstance(stmt, ast.Try):
        prev_end = stmt.body[-1].end_lineno

        for handler in stmt.handlers:
            if prev_end is not None:
                yield ("except", prev_end)

            prev_end = handler.body[-1].end_lineno

        if stmt.orelse and prev_end is not None:
            yield ("else", prev_end)
            prev_end = stmt.orelse[-1].end_lineno

        if stmt.finalbody and prev_end is not None:
            yield ("finally", prev_end)


def _walk_up_past_comments(lines: list[str], start_idx: int) -> int:
    """Return the first non-comment index at or above ``start_idx``."""
    idx = start_idx

    while idx >= 0 and lines[idx].lstrip().startswith("#"):
        idx -= 1

    return idx


def _line_above_is_blank(lines: list[str], lineno: int) -> bool:
    idx = _walk_up_past_comments(lines, lineno - 2)

    if idx < 0:
        return True

    return lines[idx].strip() == ""


def _line_below_is_blank(lines: list[str], end_lineno: int) -> bool:
    idx = end_lineno

    if idx >= len(lines):
        return True

    return lines[idx].strip() == ""


def _insert_idx_before(lines: list[str], lineno: int) -> int:
    """Return the line index where a blank should be inserted.

    If comments precede the control statement, the blank goes
    above the topmost comment so the comment block stays attached
    to the statement it documents.
    """
    return _walk_up_past_comments(lines, lineno - 2) + 1


def _is_docstring(stmt: ast.stmt) -> bool:
    """Whether ``stmt`` is a string-literal docstring expression."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _scan_body(
    body: list[ast.stmt],
    lines: list[str],
    path: Path,
) -> Iterator[tuple[str, int]]:
    """Yield ``(message, insert_index)`` per violation in ``body``."""
    last = len(body) - 1
    # PEP 257: no blank line between a function docstring and the
    # first statement that follows.  Treat a leading docstring as
    # transparent so the first "real" statement is exempt from the
    # blank-line-before rule.
    first_real = 1 if body and _is_docstring(body[0]) else 0

    for i, stmt in enumerate(body):
        if isinstance(stmt, CONTROL_TYPES):
            if i > first_real and not _line_above_is_blank(lines, stmt.lineno):
                yield (
                    f"{path}:{stmt.lineno}: missing blank line before "
                    f"`{_control_name(stmt)}`",
                    _insert_idx_before(lines, stmt.lineno),
                )

            if (
                i < last
                and stmt.end_lineno is not None
                and not _line_below_is_blank(lines, stmt.end_lineno)
            ):
                yield (
                    f"{path}:{stmt.end_lineno}: missing blank line after "
                    f"`{_control_name(stmt)}`",
                    stmt.end_lineno,
                )

            for keyword, prev_end in _branch_gaps(stmt):
                if not _line_below_is_blank(lines, prev_end):
                    yield (
                        f"{path}:{prev_end}: missing blank line before "
                        f"`{keyword}`",
                        prev_end,
                    )

        for sub_body in _iter_bodies(stmt):
            yield from _scan_body(sub_body, lines, path)


def _process(path: Path, *, fix: bool) -> tuple[list[str], bool]:
    """Scan ``path``; if ``fix``, rewrite it.  Return (messages, changed)."""
    src = path.read_text()

    try:
        tree = ast.parse(src, filename=str(path))

    except SyntaxError as exc:
        return [f"{path}: failed to parse: {exc}"], False

    lines = src.splitlines()
    messages: list[str] = []
    insertions: set[int] = set()

    for message, idx in _scan_body(tree.body, lines, path):
        messages.append(message)
        insertions.add(idx)

    if not (fix and insertions):
        return messages, False

    # Reverse order so earlier indices stay valid as we insert.
    for idx in sorted(insertions, reverse=True):
        lines.insert(idx, "")

    new_src = "\n".join(lines)

    if src.endswith("\n"):
        new_src += "\n"

    path.write_text(new_src)
    return messages, True


def main(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Paths to scan.  Defaults to src/ and tests/.",
        ),
    ] = None,
    fix: Annotated[  # noqa: FBT002 -- typer treats this as a --fix flag
        bool,
        typer.Option(
            "--fix",
            help="Insert missing blank lines in place.",
        ),
    ] = False,
) -> None:
    """Check blank lines around control structures."""
    roots = paths or [Path("src"), Path("tests")]

    all_messages: list[str] = []
    files_changed = 0

    for root in roots:
        if root.is_file():
            files = [root] if root.suffix == ".py" else []

        else:
            files = sorted(root.rglob("*.py"))

        for path in files:
            messages, changed = _process(path, fix=fix)
            all_messages.extend(messages)

            if changed:
                files_changed += 1

    for message in all_messages:
        typer.echo(message)

    if fix:
        typer.echo(
            f"\nFixed {files_changed} file(s); "
            f"{len(all_messages)} violation(s) addressed.",
            err=True,
        )
        return

    if all_messages:
        typer.echo(f"\n{len(all_messages)} violation(s)", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
