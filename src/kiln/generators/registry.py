"""Generator registry and entry-point plugin discovery."""

from __future__ import annotations

import importlib.metadata

from kiln.config.schema import KilnConfig
from kiln.generators.base import GeneratedFile, Generator
from kiln.generators.fastapi.crud import CRUDGenerator
from kiln.generators.fastapi.models import PGCraftModelGenerator
from kiln.generators.fastapi.router import RouterGenerator
from kiln.generators.fastapi.views import ViewGenerator

_BUILTIN_GENERATORS: list[type[Generator]] = [
    PGCraftModelGenerator,
    ViewGenerator,
    CRUDGenerator,
    RouterGenerator,
]


class GeneratorRegistry:
    """Registry that runs generators and discovers third-party plugins.

    Usage::

        registry = GeneratorRegistry.default()
        files = registry.run(config)

    Third-party generators are registered via the
    ``kiln.generators`` entry-point group in their
    ``pyproject.toml``::

        [project.entry-points."kiln.generators"]
        my_gen = "my_package.generators:MyGenerator"

    The class is instantiated with no arguments and must implement
    the :class:`~kiln.generators.base.Generator` protocol.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._generators: dict[str, Generator] = {}

    @classmethod
    def default(cls) -> GeneratorRegistry:
        """Return a registry pre-loaded with all built-in generators.

        Also discovers any installed third-party generators registered
        via the ``kiln.generators`` entry-point group.

        Returns:
            A :class:`GeneratorRegistry` ready to call
            :meth:`run`.
        """
        registry = cls()
        for gen_cls in _BUILTIN_GENERATORS:
            registry.register(gen_cls())
        registry.discover_entry_points()
        return registry

    def register(self, generator: Generator) -> None:
        """Add *generator* to the registry.

        If a generator with the same :attr:`~Generator.name` is
        already registered it will be replaced.

        Args:
            generator: The generator instance to register.
        """
        self._generators[generator.name] = generator

    def discover_entry_points(self) -> None:
        """Load generators registered in ``kiln.generators`` entry points.

        Each entry point must point to a class that implements the
        :class:`~kiln.generators.base.Generator` protocol and can
        be instantiated with no arguments.
        """
        for ep in importlib.metadata.entry_points(group="kiln.generators"):
            gen_cls: type[Generator] = ep.load()
            self.register(gen_cls())

    def run(self, config: KilnConfig) -> list[GeneratedFile]:
        """Run all applicable generators against *config*.

        Generators whose :meth:`~Generator.can_generate` returns
        ``False`` are skipped.

        Args:
            config: The validated kiln configuration.

        Returns:
            Flat list of :class:`~kiln.generators.base.GeneratedFile`
            objects from every generator that ran.
        """
        return [
            f
            for gen in self._generators.values()
            if gen.can_generate(config)
            for f in gen.generate(config)
        ]
