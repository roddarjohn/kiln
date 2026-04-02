"""Kiln CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from kiln.config.loader import load
from kiln.config.schema import KilnConfig
from kiln.generators.base import GeneratedFile
from kiln.generators.init.scaffold import ScaffoldGenerator
from kiln.generators.registry import GeneratorRegistry

app = typer.Typer(help="CLI for autogenerating FastAPI + pgcraft files.")


@app.callback()
def main() -> None:
    """CLI for autogenerating files from templates."""


@app.command()
def init(
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output root directory."),
    ],
) -> None:
    """Generate one-time scaffold files (db base, session, auth deps).

    Run this once when starting a new project.  Existing files are
    never overwritten.

    Example::

        kiln init --out ./src/app
    """
    scaffold = ScaffoldGenerator()
    files = scaffold.generate()
    _write_files(files, out)
    typer.echo(f"Scaffold written to {out}")


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
) -> None:
    """Generate pgcraft models, view stubs, and FastAPI routes.

    Re-running is safe: model and route files are always overwritten,
    while pgcraft stub files (db/views/) are written only on first
    creation so hand-written SQL is never destroyed.

    Example::

        kiln generate --config myapp.jsonnet --out ./src/app
    """
    try:
        cfg: KilnConfig = load(config)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(1) from exc

    registry = GeneratorRegistry.default()
    files = registry.run(cfg)
    written, skipped = _write_files(files, out)
    typer.echo(
        f"Generated {written} file(s)"
        + (f", skipped {skipped} existing stub(s)." if skipped else ".")
    )


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
