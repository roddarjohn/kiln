"""Render registry for output types.

The ``@renders`` decorator registers a function that knows how
to turn a build output into a :class:`Fragment` -- a path,
import set, and shell-template spec.  The engine/assembler
calls renderers after the build phase and then groups fragments
by output path to produce final files.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from foundry.imports import ImportCollector
from foundry.scope import Scope, ScopeTree

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
    # Lambda defers the lookup -- BuildStore is defined later in
    # this module, so ``default_factory=BuildStore`` would NameError.
    store: BuildStore = field(default_factory=lambda: BuildStore())  # noqa: PLW0108
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


#: Union of fragment types a renderer may yield.
Fragment = FileFragment | SnippetFragment


_RendererFn = Callable[[Any, RenderCtx], "Fragment | Iterable[Fragment]"]


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

        A renderer may return a single fragment, a list, or a
        generator; all are normalized to a list here.  Renderers
        typically yield a :class:`FileFragment` declaring the
        output file plus one or more :class:`SnippetFragment`
        contributions into its slots.

        Args:
            obj: The build output to render.
            ctx: Render context.

        Returns:
            A list of fragments (file + snippet).  May be empty
            if the renderer decides not to contribute.

        Raises:
            LookupError: No renderer registered for the type.

        """
        output_type = type(obj)
        fn = self._entries.get(output_type)
        if fn is None:
            msg = f"No renderer for {output_type.__name__}"
            raise LookupError(msg)
        result = fn(obj, ctx)
        if isinstance(result, FileFragment | SnippetFragment):
            return [result]
        return list(result)

    def has_renderer(self, output_type: type) -> bool:
        """Return whether any renderer is registered for *output_type*."""
        return output_type in self._entries


#: Process-wide render registry.
#:
#: Targets' renderer modules register into this singleton at
#: import time.  Because foundry discovers operations via the
#: ``foundry.operations`` entry-point group and loading an
#: operation transitively imports its renderer module, no
#: separate renderer-discovery step is needed — by the time the
#: pipeline's assembler runs, every renderer is registered.
registry = RenderRegistry()


@dataclass
class BuildStore:
    """Accumulator for objects produced during the build phase.

    Objects are keyed by ``(instance_id, op_name)``.  Instance ids
    are dot-path strings produced by the engine (e.g.
    ``"project.apps.0.resources.2"``) — the leaf scope, the
    ancestor chain, and every index are all recoverable from the
    id via :func:`foundry.scope.scope_for`, so the store never
    needs a separate scope field.

    Ancestry is tracked in :attr:`_children` — the engine records
    each instance's parent id on registration so
    :meth:`children` / :meth:`descendants_of_type` can walk the
    tree without callers reconstructing store keys.

    Attributes:
        scope_tree: :class:`ScopeTree` for the build's config.
            Required for the :meth:`scope_of` derivation (and
            therefore for ``child_scope=`` filtering on
            :meth:`children`).  Defaults to empty so ad-hoc
            store-level tests can skip it when they don't care.
        _items: Internal storage mapping ``(instance_id, op_name)``
            keys to object lists.
        _instances: Map from ``instance_id`` to the scope-instance
            config object.
        _children: Map from a parent instance id to its registered
            child instance ids, in insertion order.

    """

    scope_tree: ScopeTree = field(default_factory=ScopeTree)
    _items: dict[tuple[str, str], list[object]] = field(default_factory=dict)
    _instances: dict[str, object] = field(default_factory=dict)
    _children: dict[str, list[str]] = field(default_factory=dict)
    _parent_of: dict[str, str] = field(default_factory=dict)

    def add(
        self,
        instance_id: str,
        op_name: str,
        *objects: object,
    ) -> None:
        """Store build outputs for a build step.

        Args:
            instance_id: Dot-path id produced by the engine.
            op_name: Operation name that produced these objects.
            *objects: The build outputs to store.

        """
        self._items.setdefault((instance_id, op_name), []).extend(objects)

    def register_instance(
        self,
        instance_id: str,
        instance: object,
        *,
        parent: str | None = None,
    ) -> None:
        """Remember the scope-instance object for *instance_id*.

        Called by the engine before operations run at each scope
        instance.  The assembler looks instances up via
        :meth:`get_instance` and exposes them on ``RenderCtx`` so
        renderers can read the config object that produced each
        build entry.

        Args:
            instance_id: Dot-path id.
            instance: The scope-instance config object.
            parent: Id of the enclosing scope instance.  When
                given, :meth:`children` will surface this instance
                under *parent*.  Omit for the project root.

        """
        self._instances[instance_id] = instance
        if parent is not None:
            self._parent_of[instance_id] = parent
            siblings = self._children.setdefault(parent, [])
            if instance_id not in siblings:
                siblings.append(instance_id)

    def scope_of(self, instance_id: str) -> Scope:
        """Resolve the :class:`Scope` an ``instance_id`` belongs to."""
        return self.scope_tree.scope_for(instance_id)

    def ancestor_of(
        self,
        instance_id: str,
        scope_name: str,
    ) -> object | None:
        """Return the enclosing instance at *scope_name*, if any.

        Walks ``_parent_of`` edges from *instance_id* toward the
        root and returns the first instance whose scope name
        matches.  Used by descendant ops that need data from a
        higher scope (e.g. an operation-scope op reading its
        enclosing resource's ``model``).

        Args:
            instance_id: Id whose ancestor to find.
            scope_name: Scope name of the wanted ancestor.

        Returns:
            The ancestor instance, or ``None`` if no ancestor at
            that scope is registered.

        """
        current = self._parent_of.get(instance_id)
        while current is not None:
            if self.scope_of(current).name == scope_name:
                return self._instances.get(current)
            current = self._parent_of.get(current)
        return None

    def children(
        self,
        parent_id: str,
        *,
        child_scope: str | None = None,
    ) -> list[tuple[str, object]]:
        """Return child instances of *parent_id*.

        Children come back in registration (config) order.  When
        *child_scope* is given, only children in that scope are
        returned (requires :attr:`scopes` to be populated).

        Args:
            parent_id: Parent instance id.
            child_scope: Optional scope-name filter.

        Returns:
            List of ``(child_id, child_instance)`` pairs.

        """
        out: list[tuple[str, object]] = []
        for child_id in self._children.get(parent_id, []):
            if (
                child_scope is not None
                and self.scope_of(child_id).name != child_scope
            ):
                continue
            out.append((child_id, self._instances[child_id]))
        return out

    def descendants_of_type(
        self,
        parent_id: str,
        output_type: type,
        *,
        child_scope: str | None = None,
    ) -> list[tuple[str, object, list[object]]]:
        """Return children whose scope produced outputs of *output_type*.

        Walks the direct children of *parent_id* and returns, for
        each child with at least one matching output, its id,
        instance object, and matching items.  Used by aggregator
        ops (e.g. the app-scope router) to find which children
        contributed to a build step without reconstructing store
        keys.

        Args:
            parent_id: Parent instance id.
            output_type: Class of outputs to filter by.
            child_scope: Optional scope-name filter on children.

        Returns:
            List of ``(child_id, child_instance, items)`` for
            every child with at least one matching output.

        """
        out: list[tuple[str, object, list[object]]] = []
        for child_id, child_inst in self.children(
            parent_id,
            child_scope=child_scope,
        ):
            items = [
                item
                for item in self.get_by_instance(child_id)
                if isinstance(item, output_type)
            ]
            if items:
                out.append((child_id, child_inst, items))
        return out

    def get_instance(self, instance_id: str) -> object | None:
        """Return the instance registered for *instance_id*, if any."""
        return self._instances.get(instance_id)

    def get(self, instance_id: str, op_name: str) -> list[object]:
        """Retrieve build outputs for a specific ``(instance, op)``."""
        return list(self._items.get((instance_id, op_name), []))

    def get_by_instance(self, instance_id: str) -> list[object]:
        """Retrieve all outputs for *instance_id* across every operation."""
        result: list[object] = []
        for (stored_id, _), items in self._items.items():
            if stored_id == instance_id:
                result.extend(items)
        return result

    def outputs_under[T](
        self,
        ancestor_id: str,
        output_type: type[T],
    ) -> list[T]:
        """Return every *output_type* output at or below *ancestor_id*.

        Walks the store by path prefix, so output produced at any
        depth under *ancestor_id* surfaces — useful for ops that
        aggregate or mutate outputs from deeper scopes (e.g. auth
        adding dependencies to every handler under a resource).
        """
        prefix = f"{ancestor_id}."
        result: list[T] = []
        for (stored_id, _), items in self._items.items():
            if stored_id == ancestor_id or stored_id.startswith(prefix):
                result.extend(
                    item for item in items if isinstance(item, output_type)
                )
        return result

    def get_by_type(self, output_type: type) -> list[object]:
        """Retrieve all build outputs of a given type."""
        result: list[object] = []
        for items in self._items.values():
            result.extend(
                item for item in items if isinstance(item, output_type)
            )
        return result

    def all_items(self) -> list[object]:
        """Return every stored build output."""
        result: list[object] = []
        for items in self._items.values():
            result.extend(items)
        return result

    def entries(
        self,
    ) -> Iterator[tuple[str, str, list[object]]]:
        """Iterate stored entries as ``(instance_id, op_name, items)``.

        Used by the assembler to walk the store and dispatch each
        item to the correct renderer.
        """
        for (instance_id, op_name), items in self._items.items():
            yield instance_id, op_name, items
