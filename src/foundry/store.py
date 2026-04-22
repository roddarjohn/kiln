"""Build-phase object store.

:class:`BuildStore` accumulates the objects produced by every
operation and tracks the ancestry between scope instances so
later ops and the assembler can walk the tree without
reconstructing dot-path ids themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from foundry.scope import Scope, ScopeTree

if TYPE_CHECKING:
    from collections.abc import Iterator


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
    each instance's parent id on registration so :meth:`children`
    can walk the tree without callers reconstructing store keys.

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
        instance.  Renderers access these via
        :meth:`ancestor_of` when they need a higher scope's
        config.

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

    def entries(
        self,
    ) -> Iterator[tuple[str, str, list[object]]]:
        """Iterate stored entries as ``(instance_id, op_name, items)``.

        Used by the assembler to walk the store and dispatch each
        item to the correct renderer.
        """
        for (instance_id, op_name), items in self._items.items():
            yield instance_id, op_name, items
