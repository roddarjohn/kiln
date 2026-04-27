"""User-facing error hierarchy for the foundry CLI.

Exceptions that inherit from :class:`CLIError` are caught at the
CLI entry point (:func:`foundry.cli.cli_main`) and rendered as
``{prefix}: {message}`` with exit code 1.  Anything else
propagates with a traceback, because it signals a bug rather than
bad user input.
"""


class CLIError(Exception):
    """Base class for errors the CLI should render cleanly.

    Subclasses set :attr:`~foundry.errors.CLIError.prefix` to
    control how the error is labelled when rendered at the CLI
    boundary.
    """

    prefix: str = "Error"


class ConfigError(CLIError):
    """Raised when a config file can't be loaded or is invalid."""

    prefix = "Error loading config"


class GenerationError(CLIError):
    """Raised when file generation fails due to bad config semantics."""

    prefix = "Error"
