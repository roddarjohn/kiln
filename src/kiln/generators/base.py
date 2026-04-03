"""Base types shared by all kiln generators."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from kiln.generators._env import env

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig
    from kiln.generators._helpers import ImportCollector


@dataclass
class GeneratedFile:
    """A single file produced by a generator.

    Attributes:
        path: Output path relative to the ``--out`` directory.
        content: File contents as a string.

    """

    path: str
    content: str


@dataclass
class FileSpec:
    """Mutable specification for a generated file.

    Collects imports, exports, and template context during
    pipeline execution. Operations mutate a ``FileSpec`` by
    appending to its :attr:`imports`, :attr:`exports`, and
    :attr:`context` collections. After all operations have run,
    call :meth:`render` to produce a :class:`GeneratedFile`.

    Attributes:
        path: Output path relative to ``--out``, e.g.
            ``"myapp/schemas/user.py"``.
        template: Jinja2 template name, e.g.
            ``"fastapi/schema.py.j2"``.
        imports: :class:`~kiln.generators._helpers.ImportCollector`
            accumulating all ``import`` statements for this file.
        exports: Names this file makes available (class names,
            function names) for other files to import.
        context: Template variables beyond ``import_block``.
        package_prefix: Prefix for the dotted import path, e.g.
            ``"_generated"``.

    """

    path: str
    template: str
    imports: ImportCollector
    exports: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    package_prefix: str = ""

    @property
    def module(self) -> str:
        """Dotted Python import path for this file.

        Derives the module path from :attr:`path` and
        :attr:`package_prefix`. For example, path
        ``"myapp/schemas/user.py"`` with prefix ``"_generated"``
        yields ``"_generated.myapp.schemas.user"``.
        """
        stem = self.path.removesuffix(".py").replace("/", ".")
        if self.package_prefix:
            return f"{self.package_prefix}.{stem}"
        return stem

    def render(self) -> GeneratedFile:
        """Render the template and return a :class:`GeneratedFile`.

        Injects ``import_block`` into the template context — a
        pre-rendered string of all accumulated import statements.
        """
        import_lines = self.imports.lines()
        import_block = "\n".join(import_lines)
        if import_lines:
            import_block += "\n"
        ctx = {**self.context, "import_block": import_block}
        tmpl = env.get_template(self.template)
        return GeneratedFile(
            path=self.path,
            content=tmpl.render(**ctx),
        )


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
