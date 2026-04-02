"""Kiln CLI entry point."""

import typer

app = typer.Typer()


@app.callback()
def main() -> None:
    """CLI for autogenerating files from templates."""
