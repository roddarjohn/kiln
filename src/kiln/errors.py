"""User-facing error hierarchy for the kiln CLI.

Any exception that inherits from :class:`KilnError` is caught at
the CLI entry point (:func:`kiln.cli.cli_main`) and rendered as
``{prefix}: {message}`` with exit code 1.  Anything else --
``AttributeError``, ``TypeError`` from a programming mistake, and
so on -- propagates with a traceback, because that signals a bug
in kiln rather than bad input from the user.
"""

from __future__ import annotations


class KilnError(Exception):
    """Base class for errors that are the user's to fix.

    Subclasses set :attr:`prefix` to control how the error is
    labelled when rendered at the CLI boundary.
    """

    prefix: str = "Error"


class ConfigError(KilnError):
    """Raised when a config file can't be loaded or is invalid."""

    prefix = "Error loading config"


class GenerationError(KilnError):
    """Raised when file generation fails due to bad config semantics."""

    prefix = "Error"
