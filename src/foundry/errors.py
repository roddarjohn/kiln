"""User-facing error base class for the foundry CLI.

Exceptions that inherit from :class:`CLIError` are caught at the
CLI entry point (:func:`foundry.cli.cli_main`) and rendered as
``{prefix}: {message}`` with exit code 1.  Anything else
propagates with a traceback, because it signals a bug rather than
bad user input.

Targets define their own subclasses with target-specific
``prefix`` labels (e.g. kiln's :class:`~kiln.errors.ConfigError`).
"""

from __future__ import annotations


class CLIError(Exception):
    """Base class for errors the CLI should render cleanly.

    Subclasses set :attr:`prefix` to control how the error is
    labelled when rendered at the CLI boundary.
    """

    prefix: str = "Error"
