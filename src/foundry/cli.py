"""Foundry CLI entry point.

The CLI is target-agnostic: every piece of framework-specific
behavior comes from a :class:`~foundry.target.Target` discovered
via the ``foundry.targets`` entry-point group.  The foundry CLI
loads the config, runs the generic pipeline against the target's
registry/assembler/env, and writes files to disk.
"""

import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from foundry.config import load_config
from foundry.errors import CLIError
from foundry.output import write_files
from foundry.pipeline import generate
from foundry.target import Target, discover_targets

app = typer.Typer(
    help=(
        "Generic code-generation CLI.  Operates on any target "
        "registered under the foundry.targets entry-point group."
    ),
)
targets_app = typer.Typer(help="Inspect installed targets.")
app.add_typer(targets_app, name="targets")


ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        "-c",
        help="Path to the config file.",
    ),
]
OutOption = Annotated[
    Path,
    typer.Option(
        "--out",
        "-o",
        help="Output root directory.  Defaults to the current directory.",
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


def _stdlibs() -> dict[str, Path]:
    """Collect jsonnet stdlib prefixes from every installed target.

    Every target's stdlib is available to every config, so an
    installation with kiln + another target lets configs import
    from both under their respective prefixes.
    """
    return {
        t.name: t.jsonnet_stdlib_dir
        for t in discover_targets()
        if t.jsonnet_stdlib_dir is not None
    }


@app.callback()
def main() -> None:
    """Run the generic code-generation CLI."""


@app.command("clean")
def clean_cmd(
    config: ConfigOption,
    out: OutOption = Path(),
    target_name: TargetOption = None,
) -> None:
    """Delete the output directory.

    Removes *out* and its contents.  The current working directory
    is never deleted.  ``--config`` is parsed so the CLI surfaces
    config errors consistently, but its contents do not influence
    what is removed.
    """
    target = _resolve_target(target_name)
    load_config(config, target.schema, _stdlibs())

    if out == Path() or not out.exists():
        typer.echo(f"Nothing to clean at {out}.")
        return

    shutil.rmtree(out)
    typer.echo(f"Cleaned {out}.")


@app.command("generate")
def generate_cmd(
    config: ConfigOption,
    out: OutOption = Path(),
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
    dry_run: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "List the files that would be generated without "
                "touching the filesystem.  Incompatible with "
                "``--clean``."
            ),
        ),
    ] = False,
) -> None:
    """Generate files from a config via the selected target."""
    if dry_run and clean:
        msg = "--dry-run cannot be combined with --clean"
        raise CLIError(msg)

    if clean:
        clean_cmd(config=config, out=out, target_name=target_name)

    target = _resolve_target(target_name)
    cfg = load_config(config, target.schema, _stdlibs())
    files = generate(cfg, target)

    if dry_run:
        for f in files:
            typer.echo(str(out / f.path))
        typer.echo(f"Would generate {len(files)} file(s).")
        return

    written = write_files(files, out)
    typer.echo(f"Generated {written} file(s).")


@app.command("validate")
def validate_cmd(
    config: ConfigOption,
    target_name: TargetOption = None,
) -> None:
    """Validate a config file without generating anything.

    Parses and schema-checks ``--config`` using the selected
    target, then exits.  Useful as a pre-commit check or for
    editor integrations that want fast feedback without running
    the full pipeline.
    """
    target = _resolve_target(target_name)
    load_config(config, target.schema, _stdlibs())
    typer.echo(f"{config} is valid for target {target.name!r}.")


@targets_app.command("list")
def targets_list_cmd() -> None:
    """List every target registered under ``foundry.targets``.

    Each line is formatted as ``<name> (<language>)`` so users can
    see at a glance which target to pass to ``--target``.
    """
    targets = discover_targets()
    if not targets:
        typer.echo("No targets installed.")
        return

    for target in targets:
        typer.echo(f"{target.name} ({target.language})")


def cli_main() -> None:
    """Run the CLI, converting :class:`~foundry.errors.CLIError` cleanly.

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
