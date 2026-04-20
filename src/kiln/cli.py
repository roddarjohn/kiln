"""Kiln CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from kiln.config.loader import load
from kiln.generators.registry import GeneratorRegistry
from kiln_core.output import write_files

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig

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
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help=(
                "Output root directory.  Defaults to the config's "
                "``package_prefix`` value (e.g. ``_generated``) or "
                "the current directory when prefix is empty."
            ),
        ),
    ] = None,
    clean: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--clean",
            help="Delete all contents of --out before generating.",
        ),
    ] = False,
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

    Use ``--clean`` to delete the output directory first, which removes any
    files that no longer correspond to the current config.

    Example::

        kiln generate --config project.jsonnet --out src/
        kiln generate --config blog.jsonnet --out src/ --clean
    """
    try:
        cfg: KilnConfig = load(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(1) from exc

    effective_out: Path = (
        out if out is not None else Path(cfg.package_prefix or ".")
    )

    try:
        registry = GeneratorRegistry.default()
        files = registry.run(cfg)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    written = write_files(files, effective_out, clean=clean)
    typer.echo(f"Generated {written} file(s).")
