"""Kiln CLI entry point."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from kiln.config.loader import load
from kiln.generators.registry import GeneratorRegistry

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig
    from kiln.generators.base import GeneratedFile

app = typer.Typer(help="CLI for autogenerating FastAPI + pgcraft files.")


@app.callback()
def main() -> None:
    """CLI for autogenerating files from templates."""


@app.command()
def generate(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to .json or .jsonnet config file.",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output root directory."),
    ],
    no_validate: Annotated[  # noqa: ARG001,FBT002
        bool,
        typer.Option(
            "--no-validate",
            help=(
                "Deprecated — validation is no longer performed. "
                "Accepted for backwards compatibility but has no effect."
            ),
        ),
    ] = False,
) -> None:
    """Generate all project files from a config.

    Handles both app-level configs (models + routes) and project-level
    configs (multi-app, with auth and database scaffolding).  Re-running
    is always safe — all files are overwritten.

    Example::

        kiln generate --config project.jsonnet --out src/
        kiln generate --config blog.jsonnet --out src/
    """
    try:
        cfg: KilnConfig = load(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        registry = GeneratorRegistry.default()
        files = registry.run(cfg)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    written, _skipped = _write_files(files, out)
    typer.echo(f"Generated {written} file(s).")


def _write_files(
    files: list[GeneratedFile],
    out_dir: Path,
) -> tuple[int, int]:
    """Write *files* under *out_dir*, respecting the overwrite flag.

    Args:
        files: Files to write.
        out_dir: Root directory for output paths.

    Returns:
        ``(written, skipped)`` counts.

    """
    written = 0
    skipped = 0
    for f in files:
        target = out_dir / f.path
        if target.exists() and not f.overwrite:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        written += 1
    return written, skipped
