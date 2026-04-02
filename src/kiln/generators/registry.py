"""Generator registry and entry-point plugin discovery."""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig
    from kiln.generators.base import GeneratedFile, Generator
from kiln.generators.fastapi.crud import CRUDGenerator
from kiln.generators.fastapi.models import PGCraftModelGenerator
from kiln.generators.fastapi.project_router import ProjectRouterGenerator
from kiln.generators.fastapi.router import RouterGenerator
from kiln.generators.fastapi.views import ViewGenerator
from kiln.generators.init.scaffold import ScaffoldGenerator

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

    In **project mode** (``config.apps`` is non-empty) the registry:

    1. Generates ``db/`` and ``auth/`` scaffold from the project config.
    2. Runs all app-level generators for each app, merging the project's
       ``auth`` and ``databases`` into each app config.
    3. Generates a root ``routes/__init__.py`` that mounts all app routers.

    In **app mode** (``config.apps`` is empty) the registry:

    1. Generates ``db/`` and ``auth/`` scaffold if the config includes
       ``auth`` or ``databases``.
    2. Runs all app-level generators against the config directly.

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
            A :class:`GeneratorRegistry` ready to call :meth:`run`.

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

        Automatically detects project mode vs app mode based on whether
        ``config.apps`` is populated.  See class docstring for details.

        Args:
            config: The validated kiln configuration.

        Returns:
            Flat list of :class:`~kiln.generators.base.GeneratedFile`
            objects from every generator that ran.

        """
        if config.apps:
            return self._run_project(config)
        return self._run_app(config)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_project(self, config: KilnConfig) -> list[GeneratedFile]:
        """Project mode: scaffold + per-app generation + root router."""
        files: list[GeneratedFile] = []
        files.extend(ScaffoldGenerator().generate(config))
        for app_ref in config.apps:
            app_config = app_ref.config.model_copy(
                update={"auth": config.auth, "databases": config.databases}
            )
            files.extend(self._run_app_generators(app_config))
        proj_router = ProjectRouterGenerator()
        if proj_router.can_generate(config):
            files.extend(proj_router.generate(config))
        return files

    def _run_app(self, config: KilnConfig) -> list[GeneratedFile]:
        """App mode: optional scaffold + app generators."""
        files: list[GeneratedFile] = []
        if config.auth is not None or config.databases:
            files.extend(ScaffoldGenerator().generate(config))
        files.extend(self._run_app_generators(config))
        return files

    def _run_app_generators(self, config: KilnConfig) -> list[GeneratedFile]:
        """Run all registered app-level generators against *config*."""
        return [
            f
            for gen in self._generators.values()
            if gen.can_generate(config)
            for f in gen.generate(config)
        ]
