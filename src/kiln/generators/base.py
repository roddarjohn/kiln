"""Kiln-specific generator protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig
    from kiln_core import GeneratedFile


@runtime_checkable
class Generator(Protocol):
    """Protocol every kiln generator must satisfy.

    Third-party generators can be registered via the
    ``kiln.generators`` entry-point group::

        [project.entry-points."kiln.generators"]
        my_gen = "my_package.generators:MyGenerator"

    The class will be instantiated with no arguments, so
    ``__init__`` must not require any parameters.
    """

    @property
    def name(self) -> str:
        """Unique identifier for this generator."""
        ...

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True if this generator has work to do for *config*.

        Args:
            config: The validated kiln configuration.

        Returns:
            ``True`` when the generator should run.

        """
        ...

    def generate(
        self,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Produce files from *config*.

        Args:
            config: The validated kiln configuration.

        Returns:
            List of :class:`GeneratedFile` objects.  Paths are
            relative to the output directory chosen by the caller.

        """
        ...
