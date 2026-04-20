"""Base types shared by all kiln generators.

Re-exports :class:`~kiln_core.spec.FileSpec` and
:class:`~kiln_core.spec.GeneratedFile` from :mod:`kiln_core`
and defines the kiln-specific :class:`Generator` protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from kiln_core.spec import FileSpec, GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig

# Re-export so existing ``from kiln.generators.base import ...``
# continues to work.
__all__ = ["FileSpec", "GeneratedFile", "Generator"]


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

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Produce files from *config*.

        Args:
            config: The validated kiln configuration.

        Returns:
            List of :class:`GeneratedFile` objects.  Paths are
            relative to the output directory chosen by the caller.

        """
        ...
