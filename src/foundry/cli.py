"""Foundry CLI entry point.

The CLI is target-agnostic: every piece of framework-specific
behavior comes from a :class:`~foundry.target.Target` discovered
via the ``foundry.targets`` entry-point group.  The foundry CLI
itself only knows how to load a config, dispatch to the target's
generator, and write files to disk.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from foundry.errors import CLIError
from foundry.output import write_files
from foundry.target import discover_targets

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.target import Target


app = typer.Typer(
    help=(
        "Generic code-generation CLI.  Operates on any target "
        "registered under the foundry.targets entry-point group."
    ),
)


ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        "-c",
        help="Path to the config file.",
    ),
]
OutOption = Annotated[
    Path | None,
    typer.Option(
        "--out",
        "-o",
        help=(
            "Output root directory.  Defaults to the target's "
            "own default (e.g. kiln's ``package_prefix``) or the "
            "current directory when no default is set."
        ),
    ),
]
TargetOption = Annotated[
    str | None,
    typer.Option(
        "--target",
        "-t",
        help=(
            "Which target to use.  Optional when exactly one "
            "target is installed."
        ),
    ),
]


def _resolve_target(name: str | None) -> Target:
    """Pick a :class:`~foundry.target.Target` by name or uniqueness.

    Args:
        name: Name passed via ``--target``.  ``None`` means the
            user did not specify one.

    Returns:
        The matching target.

    Raises:
        CLIError: If no targets are registered, if ``name`` is
            unknown, or if ``name`` is ``None`` and multiple
            targets are installed.

    """
    targets = discover_targets()
    if not targets:
        msg = (
            "No target is registered under the foundry.targets "
            "entry-point group.  Install a plugin (e.g. kiln) "
            "that provides one."
        )
        raise CLIError(msg)
    if name is None:
        if len(targets) > 1:
            names = ", ".join(t.name for t in targets)
            msg = (
                f"Multiple targets installed ({names}); pick one with --target"
            )
            raise CLIError(msg)
        return targets[0]
    for target in targets:
        if target.name == name:
            return target
    names = ", ".join(t.name for t in targets)
    msg = f"No target named {name!r} (installed: {names})"
    raise CLIError(msg)


def _effective_out(
    target: Target,
    cfg: BaseModel,
    out: Path | None,
) -> Path:
    """Resolve the effective output directory.

    ``--out`` wins when supplied, otherwise the target's
    ``default_out`` policy is consulted, otherwise the current
    directory is used.
    """
    if out is not None:
        return out
    if target.default_out is not None:
        return target.default_out(cfg) or Path()
    return Path()


@app.callback()
def main() -> None:
    """Run the generic code-generation CLI."""


@app.command("clean")
def clean_cmd(
    config: ConfigOption,
    out: OutOption = None,
    target_name: TargetOption = None,
) -> None:
    """Delete the output directory for a config.

    Resolves the output directory the same way :func:`generate_cmd`
    does (``--out`` wins, else the target's default) and removes
    it.  The current working directory is never deleted.
    """
    target = _resolve_target(target_name)
    cfg = target.load_config(config)
    effective_out = _effective_out(target, cfg, out)

    if effective_out == Path() or not effective_out.exists():
        typer.echo(f"Nothing to clean at {effective_out}.")
        return

    shutil.rmtree(effective_out)
    typer.echo(f"Cleaned {effective_out}.")


@app.command("generate")
def generate_cmd(
    config: ConfigOption,
    out: OutOption = None,
    target_name: TargetOption = None,
    clean: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--clean",
            help=(
                "Run ``clean`` before generating, removing files "
                "that no longer correspond to the config."
            ),
        ),
    ] = False,
) -> None:
    """Generate files from a config via the selected target."""
    if clean:
        clean_cmd(config=config, out=out, target_name=target_name)

    target = _resolve_target(target_name)
    cfg = target.load_config(config)
    effective_out = _effective_out(target, cfg, out)
    files = target.generate(cfg)
    written = write_files(files, effective_out)

    typer.echo(f"Generated {written} file(s).")


def cli_main() -> None:
    """Run the CLI, converting :class:`CLIError` to a clean exit.

    Any ``CLIError`` raised inside a command is rendered as
    ``{prefix}: {message}`` on stderr and exits with code 1.
    Other exceptions propagate with a traceback, because they
    indicate a bug rather than bad user input.
    """
    try:
        app()
    except CLIError as exc:
        typer.echo(f"{exc.prefix}: {exc}", err=True)
        sys.exit(1)
