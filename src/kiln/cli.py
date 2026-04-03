"""Kiln CLI entry point."""

from __future__ import annotations

import shutil
from pathlib import Path
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

    if clean and effective_out.exists() and effective_out != Path():
        shutil.rmtree(effective_out)

    try:
        registry = GeneratorRegistry.default()
        files = registry.run(cfg)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    written = _write_files(files, effective_out)
    typer.echo(f"Generated {written} file(s).")


def _write_files(files: list[GeneratedFile], out_dir: Path) -> int:
    """Write *files* under *out_dir*, always overwriting existing files.

    Args:
        files: Files to write.
        out_dir: Root directory for output paths.

    Returns:
        Number of files written.

    """
    written = 0
    for f in files:
        target = out_dir / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        written += 1
    return written
