"""Smoke test for the CLI entry point."""

from typer.testing import CliRunner

from kiln.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "autogenerating" in result.output
