"""Kiln CLI entry point."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from foundry.output import write_files
from kiln.config.loader import load
from kiln.errors import KilnError
from kiln.renderers.generate import generate

if TYPE_CHECKING:
    from kiln.config.schema import ProjectConfig

app = typer.Typer(help="CLI for autogenerating FastAPI + pgcraft files.")


ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        "-c",
        help="Path to .json or .jsonnet config file.",
    ),
]
OutOption = Annotated[
    Path | None,
    typer.Option(
        "--out",
        "-o",
        help=(
            "Output root directory.  Defaults to the "
            "config's ``package_prefix`` value (e.g. "
            "``_generated``) or the current directory "
            "when prefix is empty."
        ),
    ),
]


@app.callback()
def main() -> None:
    """CLI for autogenerating files from templates."""


def _resolve(config: Path, out: Path | None) -> tuple[ProjectConfig, Path]:
    """Load the config and resolve the effective output directory.

    ``--out`` wins when provided, otherwise falls back to the
    config's ``package_prefix`` (or the current directory when
    the prefix is empty).
    """
    cfg = load(config)
    effective_out = out if out is not None else Path(cfg.package_prefix or ".")
    return cfg, effective_out


@app.command("clean")
def clean_cmd(
    config: ConfigOption,
    out: OutOption = None,
) -> None:
    """Delete the output directory for a config.

    Resolves the output directory the same way :func:`generate_cmd`
    does (``--out`` wins, else the config's ``package_prefix``) and
    removes it.  The current working directory is never deleted.

    Example::

        kiln clean --config project.jsonnet --out src/
    """
    _, effective_out = _resolve(config, out)

    if effective_out == Path() or not effective_out.exists():
        typer.echo(f"Nothing to clean at {effective_out}.")
        return

    shutil.rmtree(effective_out)
    typer.echo(f"Cleaned {effective_out}.")


@app.command("generate")
def generate_cmd(
    config: ConfigOption,
    out: OutOption = None,
    clean: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--clean",
            help=(
                "Run ``kiln clean`` before generating, removing "
                "files that no longer correspond to the config."
            ),
        ),
    ] = False,
) -> None:
    """Generate all project files from a config.

    Re-running is always safe -- all files are overwritten.

    Use ``--clean`` to invoke :func:`clean_cmd` first, which removes
    files that no longer correspond to the current config.

    Example::

        kiln generate --config project.jsonnet --out src/
        kiln generate --config blog.jsonnet --out src/ --clean
    """
    if clean:
        clean_cmd(config=config, out=out)

    cfg, effective_out = _resolve(config, out)
    files = generate(cfg)
    written = write_files(files, effective_out)

    typer.echo(f"Generated {written} file(s).")


def cli_main() -> None:
    """Run the CLI, converting :class:`KilnError` to a clean exit.

    Any ``KilnError`` raised inside a command is rendered as
    ``{prefix}: {message}`` on stderr and exits with code 1.
    Other exceptions propagate with a traceback, because they
    indicate a bug rather than bad user input.
    """
    try:
        app()

    except KilnError as exc:
        typer.echo(f"{exc.prefix}: {exc}", err=True)
        sys.exit(1)
