"""Render registry for output types.

The ``@renders`` decorator registers a function that knows how
to turn a build output into a :class:`Fragment` -- a path,
import set, and shell-template spec.  The engine/assembler
calls renderers after the build phase and then groups fragments
by output path to produce final files.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from foundry.imports import ImportCollector

if TYPE_CHECKING:
    from collections.abc import Mapping

    import jinja2


@dataclass(frozen=True)
class RenderCtx:
    """Context passed to every renderer function.

    Attributes:
        env: Jinja2 environment for template lookups.
        config: The full project config dict (or model).
        package_prefix: Dotted prefix for generated imports,
            e.g. ``"_generated"``.
        extras: Per-dispatch extras supplied by the assembler,
            typically the current scope instance (e.g.
            ``{"resource": <ResourceConfig>}``) so that renderers
            can derive paths and imports without the assembler
            having to know per-type details.

    """

    env: jinja2.Environment
    config: Any
    package_prefix: str = ""
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Fragment:
    """A single renderer's contribution to one output file.

    The assembler groups fragments by :attr:`path` and produces
    the final file by rendering :attr:`shell_template` with a
    merged :attr:`shell_context`.  Imports from every fragment
    targeting the same path are unioned.  In the merged context,
    list-valued entries with the same key are concatenated in
    fragment order; scalar entries keep the first seen value.

    Attributes:
        path: Output path relative to the output directory.
        shell_template: Jinja2 template name that wraps the
            accumulated content (e.g. ``"fastapi/route.py.j2"``).
        shell_context: Template variables.  List values for the
            same key across fragments concatenate; scalar values
            are first-write-wins.
        imports: Imports this fragment requires; merged with other
            fragments at assembly time.

    """

    path: str
    shell_template: str
    shell_context: dict[str, Any] = field(default_factory=dict)
    imports: ImportCollector = field(default_factory=ImportCollector)


_RendererFn = Callable[[Any, RenderCtx], "Fragment | list[Fragment]"]


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
        """Produce :class:`Fragment` objects for a build output.

        A renderer may return a single fragment or a list of
        fragments; both forms are normalized here.  Multiple
        fragments let one output contribute to more than one
        file (e.g. a serializer emits both its serializer file
        and an auxiliary fragment enriching the test file).

        Args:
            obj: The build output to render.
            ctx: Render context with env, config, and per-scope
                extras.

        Returns:
            A list of :class:`Fragment` objects, each targeting
            some output file.  May be empty if the renderer
            decides not to contribute.

        Raises:
            LookupError: No renderer registered for the type.

        """
        output_type = type(obj)
        fn = self._entries.get(output_type)
        if fn is None:
            msg = f"No renderer for {output_type.__name__}"
            raise LookupError(msg)
        result = fn(obj, ctx)
        if isinstance(result, Fragment):
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


#: Store key for a scope instance — ``(scope_name, instance_id)``.
InstanceKey = tuple[str, str]


@dataclass
class BuildStore:
    """Accumulator for objects produced during the build phase.

    Objects are keyed by ``(scope_name, instance_id, op_name)``
    so the engine and later operations can query earlier output.
    The store also retains a map from ``(scope, instance_id)`` to
    the scope instance object that produced the entries, so the
    assembler can attach scope-specific context to ``RenderCtx``
    without target-specific lookups.

    Scope-instance ancestry is tracked independently: when the
    engine registers an instance it may pass the parent
    :data:`InstanceKey`, and :meth:`children` /
    :meth:`descendants_of_type` let ops walk the tree without
    knowing how ``instance_id`` strings are derived.

    Attributes:
        _items: Internal storage mapping keys to object lists.
        _instances: Map from ``(scope, instance_id)`` to the scope
            instance object (populated by the engine).
        _children: Map from a parent :data:`InstanceKey` to its
            registered child keys, in insertion order.  Populated
            by :meth:`register_instance` when a parent is provided.

    """

    _items: dict[tuple[str, str, str], list[object]] = field(
        default_factory=dict
    )
    _instances: dict[InstanceKey, object] = field(default_factory=dict)
    _children: dict[InstanceKey, list[InstanceKey]] = field(
        default_factory=dict
    )

    def add(
        self,
        scope: str,
        instance_id: str,
        op_name: str,
        *objects: object,
    ) -> None:
        """Store build outputs for a build step.

        Args:
            scope: Scope name (e.g. ``"resource"``).
            instance_id: Instance identifier within the scope.
            op_name: Operation name that produced these objects.
            *objects: The build outputs to store.

        """
        key = (scope, instance_id, op_name)
        self._items.setdefault(key, []).extend(objects)

    def register_instance(
        self,
        scope: str,
        instance_id: str,
        instance: object,
        *,
        parent: InstanceKey | None = None,
    ) -> None:
        """Remember the scope instance object for a ``(scope, iid)``.

        Called by the engine before operations run at each scope
        instance.  The assembler looks instances up via
        :meth:`get_instance` and exposes them on ``RenderCtx`` so
        renderers can read the config object that produced each
        build entry.

        Args:
            scope: Scope name.
            instance_id: Instance identifier.
            instance: The scope-instance config object.
            parent: When given, record this instance as a child of
                ``parent`` so it surfaces via :meth:`children`.
                Omit for root-scope instances.

        """
        self._instances[scope, instance_id] = instance
        if parent is not None:
            siblings = self._children.setdefault(parent, [])
            child_key = (scope, instance_id)
            if child_key not in siblings:
                siblings.append(child_key)

    def children(
        self,
        parent_scope: str,
        parent_id: str,
        *,
        child_scope: str | None = None,
    ) -> list[tuple[InstanceKey, object]]:
        """Return child instances of ``(parent_scope, parent_id)``.

        Children come back in registration (config) order.  When
        *child_scope* is given, only children in that scope are
        returned.

        Args:
            parent_scope: Parent scope name.
            parent_id: Parent instance id.
            child_scope: Optional scope-name filter.

        Returns:
            List of ``((child_scope, child_id), child_instance)``.

        """
        out: list[tuple[InstanceKey, object]] = []
        for child_key in self._children.get((parent_scope, parent_id), []):
            if child_scope is not None and child_key[0] != child_scope:
                continue
            out.append((child_key, self._instances[child_key]))
        return out

    def descendants_of_type(
        self,
        parent_scope: str,
        parent_id: str,
        output_type: type,
        *,
        child_scope: str | None = None,
    ) -> list[tuple[InstanceKey, object, list[object]]]:
        """Return children whose scope produced outputs of *output_type*.

        Walks the direct children of ``(parent_scope, parent_id)``
        and returns, for each child with at least one matching
        output, the child's key, its instance object, and the
        matching items.  Used by aggregator ops (e.g. the app-scope
        router) to find which children contributed to a build step
        without reconstructing store keys.

        Args:
            parent_scope: Parent scope name.
            parent_id: Parent instance id.
            output_type: Class of outputs to filter by.
            child_scope: Optional scope-name filter on children.

        Returns:
            List of ``((child_scope, child_id), child_instance,
            items)`` for every child with at least one matching
            output.

        """
        out: list[tuple[InstanceKey, object, list[object]]] = []
        for child_key, child_inst in self.children(
            parent_scope,
            parent_id,
            child_scope=child_scope,
        ):
            items = [
                item
                for item in self.get_by_scope(*child_key)
                if isinstance(item, output_type)
            ]
            if items:
                out.append((child_key, child_inst, items))
        return out

    def get_instance(
        self,
        scope: str,
        instance_id: str,
    ) -> object | None:
        """Return the instance registered for ``(scope, iid)``, if any."""
        return self._instances.get((scope, instance_id))

    def get(
        self,
        scope: str,
        instance_id: str,
        op_name: str,
    ) -> list[object]:
        """Retrieve build outputs for a specific build step.

        Args:
            scope: Scope name.
            instance_id: Instance identifier.
            op_name: Operation name.

        Returns:
            List of build outputs, empty if none stored.

        """
        return list(self._items.get((scope, instance_id, op_name), []))

    def get_by_scope(
        self,
        scope: str,
        instance_id: str,
    ) -> list[object]:
        """Retrieve all build outputs for a scope instance.

        Args:
            scope: Scope name.
            instance_id: Instance identifier.

        Returns:
            All build outputs across all operations for this
            scope instance.

        """
        result: list[object] = []
        for (stored_scope, stored_id, _), items in self._items.items():
            if stored_scope == scope and stored_id == instance_id:
                result.extend(items)
        return result

    def get_by_type(self, output_type: type) -> list[object]:
        """Retrieve all build outputs of a given type.

        Args:
            output_type: The output class to filter by.

        Returns:
            All matching build outputs across all keys.

        """
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
    ) -> Iterator[tuple[str, str, str, list[object]]]:
        """Iterate stored entries as ``(scope, instance_id, op_name, items)``.

        Used by the assembler to walk the store and dispatch
        each item to the correct renderer with scope-aware context.
        """
        for (scope, instance_id, op_name), items in self._items.items():
            yield scope, instance_id, op_name, items
