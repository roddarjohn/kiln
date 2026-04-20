"""Mutable file specifications and rendered output types.

:class:`FileSpec` is the central abstraction: a mutable
description of a file being built up by multiple contributors.
Once all contributors have run, call :meth:`FileSpec.render` to
produce an immutable :class:`GeneratedFile`.

:func:`wire_exports` connects specs by scanning context text
for export references and adding the corresponding imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import jinja2

    from kiln_core.imports import ImportCollector


@dataclass(frozen=True)
class GeneratedFile:
    """Immutable final output -- a path and its content.

    Attributes:
        path: Output path relative to the output directory.
        content: File contents as a string.

    """

    path: str
    content: str


@dataclass
class FileSpec:
    """Mutable specification for a generated file.

    Collects imports, exports, and template context during
    pipeline execution.  Contributors mutate a ``FileSpec`` by
    appending to its :attr:`imports`, :attr:`exports`, and
    :attr:`context` collections.  After all contributors have
    run, call :meth:`render` to produce a :class:`GeneratedFile`.

    Attributes:
        path: Output path relative to ``--out``, e.g.
            ``"myapp/schemas/user.py"``.
        template: Jinja2 template name, e.g.
            ``"fastapi/schema.py.j2"``.
        imports: :class:`~kiln_core.imports.ImportCollector`
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
        :attr:`package_prefix`.  For example, path
        ``"myapp/schemas/user.py"`` with prefix ``"_generated"``
        yields ``"_generated.myapp.schemas.user"``.
        """
        stem = self.path.removesuffix(".py").replace("/", ".")
        if self.package_prefix:
            return f"{self.package_prefix}.{stem}"
        return stem

    def render(self, env: jinja2.Environment) -> GeneratedFile:
        """Render the template and return a :class:`GeneratedFile`.

        Injects ``import_block`` into the template context -- a
        pre-rendered string of all accumulated import statements.

        Args:
            env: The Jinja2 environment to use for template
                lookup and rendering.

        Returns:
            A :class:`GeneratedFile` with the rendered content.

        """
        import_lines = self.imports.lines()
        import_block = "\n".join(import_lines)
        if import_lines:
            import_block += "\n"
        ctx = {**self.context, "import_block": import_block}
        tmpl = env.get_template(self.template)
        content = tmpl.render(**ctx).rstrip() + "\n"
        return GeneratedFile(
            path=self.path,
            content=content,
        )


# -------------------------------------------------------------------
# Cross-file wiring
# -------------------------------------------------------------------


def wire_exports(specs: dict[str, FileSpec]) -> None:
    """Wire imports between specs based on export references.

    For each pair of specs where *src* appears before *dst* in
    insertion order, scans *dst*'s context values for occurrences
    of *src*'s export names and adds the corresponding import.

    This handles the common case where a route file references
    schema class names in its handler text.  Edge cases (e.g.
    names constructed at template render time) must be wired
    explicitly by the caller.

    Args:
        specs: Ordered dict of file specs.  Insertion order
            determines which specs can import from which.

    """
    spec_list = list(specs.items())
    for i, (_, dst) in enumerate(spec_list):
        text = _flatten_context(dst.context)
        for _, src in spec_list[:i]:
            for name in src.exports:
                if name in text:
                    dst.imports.add_from(src.module, name)


def _flatten_context(obj: object) -> str:
    """Recursively collect all string values from *obj*."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_flatten_context(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_flatten_context(v) for v in obj)
    return ""
