"""Render registry for output types.

The ``@renders`` decorator registers a function that knows how
to turn a build output into a :class:`Fragment` -- a path,
import set, and shell-template spec.  The engine/assembler
calls renderers after the build phase and then groups fragments
by output path to produce final files.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from foundry.env import render_template
from foundry.imports import ImportCollector
from foundry.store import BuildStore

if TYPE_CHECKING:
    import jinja2


@dataclass(frozen=True)
class RenderCtx:
    """Context passed to every renderer function.

    Attributes:
        env: Jinja2 environment for template lookups.
        config: The full project config dict (or model).
        package_prefix: Dotted prefix for generated imports,
            e.g. ``"_generated"``.
        language: Target language identifier used to render
            import blocks (e.g. ``"python"``).  Must match a
            formatter declared in the ``foundry.import_formatters``
            entry-point group.
        store: The build store.  Renderers reach ancestor scope
            instances through it (e.g. a handler rendered at
            operation scope looks up its resource via
            ``store.ancestor_of(instance_id, "resource")``).
        instance_id: Id of the scope instance whose output is
            being rendered.  Paired with :attr:`store` for
            ancestor and self lookups.

    """

    env: jinja2.Environment
    config: Any
    package_prefix: str = ""
    language: str = ""
    store: BuildStore = field(default_factory=BuildStore)
    instance_id: str = ""


@dataclass
class FileFragment:
    """Declares an output file's wrapper template and scalar context.

    One :class:`FileFragment` per output path describes the
    template the assembler wraps the file in and the non-slot
    context passed to it.  Every :class:`SnippetFragment`
    sharing that path contributes a slot-list item that the
    assembler folds into :attr:`context` before the wrapper is
    rendered.

    Multiple renderers may emit a :class:`FileFragment` for the
    same path (e.g. every route handler at the resource declares
    the route file) — the assembler requires them to agree on
    :attr:`template` and unifies their :attr:`context` dicts,
    raising if two disagree on a shared key.

    A blank :attr:`template` is a convention for an empty-content
    file (e.g. ``__init__.py``).

    Attributes:
        path: Output path relative to the output directory.
        template: Jinja2 template name that wraps the file.
        context: Non-slot template variables.  Merged across all
            FileFragments at this path (shared keys must agree).
        imports: Imports the wrapper itself needs, on top of any
            contributed by snippets.

    """

    path: str
    template: str
    context: dict[str, Any] = field(default_factory=dict)
    imports: ImportCollector = field(default_factory=ImportCollector)

    def __or__(self, other: FileFragment) -> FileFragment:
        """Merge two FileFragments targeting the same path.

        Raises :class:`ValueError` if the two fragments disagree
        on :attr:`template`, or if any shared :attr:`context` key
        has two different values.  Imports union.
        """
        if self.template != other.template:
            msg = (
                f"FileFragment template mismatch at {self.path!r}: "
                f"{self.template!r} vs {other.template!r}"
            )
            raise ValueError(msg)

        for key in self.context.keys() & other.context.keys():
            if self.context[key] != other.context[key]:
                msg = (
                    f"FileFragment context conflict at {self.path!r} "
                    f"for {key!r}: {self.context[key]!r} vs "
                    f"{other.context[key]!r}"
                )
                raise ValueError(msg)

        return FileFragment(
            path=self.path,
            template=self.template,
            context=self.context | other.context,
            imports=self.imports | other.imports,
        )


@dataclass
class SnippetFragment:
    """A contribution slotted into a file's context list.

    Each snippet becomes one entry in ``file.context[slot]`` — a
    list the wrapper template iterates over.  Snippets at the
    same path may target different slots.

    Supply exactly one of :attr:`template` (rendered by the
    assembler into a string) or :attr:`value` (used as-is, may
    be any type — useful for dict slots the wrapper iterates
    over itself).

    Attributes:
        path: Output path; must match a :class:`FileFragment`.
        slot: Key in the file's context this snippet appends to.
        template: Jinja2 template the assembler renders against
            :attr:`context` to produce a string slot item.
            Mutually exclusive with :attr:`value`.
        context: Template variables for :attr:`template`.
        value: Raw slot item — any type, used as-is.  Mutually
            exclusive with :attr:`template`.
        imports: Imports this contribution needs in the output
            file's import block.

    """

    path: str
    slot: str
    template: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    value: Any = None
    imports: ImportCollector = field(default_factory=ImportCollector)

    def render_slot_item(self, env: jinja2.Environment) -> object:
        """Return the slot-list item this snippet contributes.

        When :attr:`template` is set the assembler renders it
        against :attr:`context` and strips surrounding whitespace,
        so the surrounding file template can join items with its
        own separators without fighting jinja's trailing
        newline.  Otherwise :attr:`value` is passed through
        unchanged.
        """
        if self.template is not None:
            return render_template(
                env=env,
                template_name=self.template,
                **self.context,
            ).strip()

        return self.value


#: Union of fragment types a renderer may yield.
Fragment = FileFragment | SnippetFragment


_RendererFn = Callable[[Any, RenderCtx], "Iterable[Fragment]"]


@dataclass
class RenderRegistry:
    """Maps output types to renderer functions.

    Example::

        registry = RenderRegistry()

        @registry.renders(RouteHandler)
        def render_route(handler, ctx):
            return Fragment(...)

    """

    _entries: dict[type, _RendererFn] = field(default_factory=dict)

    def renders(
        self,
        output_type: type,
    ) -> Callable[[_RendererFn], _RendererFn]:
        """Register a renderer for *output_type*.

        Args:
            output_type: The output class this renderer handles.

        Returns:
            The original function, unmodified.

        """

        def decorator(fn: _RendererFn) -> _RendererFn:
            self._entries[output_type] = fn
            return fn

        return decorator

    def render(
        self,
        obj: object,
        ctx: RenderCtx,
    ) -> list[Fragment]:
        """Produce fragments for a build output.

        Every registered renderer returns an iterable of
        fragments (typically as a generator via ``yield``).
        Renderers usually yield a :class:`FileFragment`
        declaring the output file plus one or more
        :class:`SnippetFragment` contributions into its slots.

        Args:
            obj: The build output to render.
            ctx: Render context.

        Returns:
            A list of fragments.  May be empty if the renderer
            decides not to contribute.

        Raises:
            LookupError: No renderer registered for the type.

        """
        output_type = type(obj)
        fn = self._entries.get(output_type)

        if fn is None:
            msg = f"No renderer for {output_type.__name__}"
            raise LookupError(msg)

        return list(fn(obj, ctx))


#: Process-wide render registry.
#:
#: Targets' renderer modules register into this singleton at
#: import time.  Because foundry discovers operations via the
#: ``foundry.operations`` entry-point group and loading an
#: operation transitively imports its renderer module, no
#: separate renderer-discovery step is needed — by the time the
#: pipeline's assembler runs, every renderer is registered.
registry = RenderRegistry()
