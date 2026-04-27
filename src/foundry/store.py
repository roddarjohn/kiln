"""Build-phase object store.

:class:`BuildStore` is the shared scratchpad an
:class:`~foundry.engine.Engine` run passes between operations.
Every ``@operation`` :meth:`build` method emits zero or more
objects; those objects land in the store keyed by the scope
instance that produced them.  The assembler later walks the store
to render files, and other operations can query and mutate what
earlier operations emitted.

Key terms
---------

- **instance id** — the engine's dot-path identifier for a scope
  instance, e.g. ``"project.apps.0.resources.2.operations.1"``.
  The leaf scope, the ancestor chain, and every index are all
  recoverable from the id; callers never reconstruct these strings
  themselves — :meth:`children`, :meth:`ancestor_of`, and
  :meth:`ancestor_id_of` walk the tree for you.
- **op name** — the :attr:`~foundry.operation.OperationMeta.name`
  of the op that produced a given output, recorded alongside the
  output in the store.  Used by ops that want to find outputs from
  a specific producer.
- **output type** — the Python class of the stored object.  Every
  query method takes an ``output_type`` and returns only
  ``isinstance`` matches, so ops can narrow by what they expect.

What operations typically do
----------------------------

1. **Emit outputs** by yielding from :meth:`build`; the engine
   calls :meth:`add` on their behalf, keyed by the op's instance
   id and op name.  No direct store interaction needed for the
   common case.
2. **Look up ancestor config** with :meth:`ancestor_of` — e.g. an
   operation-scope op reading the resource's ``model`` field.
3. **Walk descendants' outputs** with :meth:`outputs_under` — e.g.
   a resource-scope ``after_children=True`` op augmenting every
   route handler under it (see
   :class:`~kiln.operations.auth.Auth`).
4. **Reach outputs an ancestor emitted** with
   :meth:`outputs_under_ancestor` / :meth:`output_under_ancestor`
   — e.g. a nested modifier op amending its parent op's outputs
   (see :class:`~kiln.operations.filter.Filter`).

Mutability
----------

Stored outputs are the live objects the assembler eventually
renders.  A later op that wants to augment an earlier op's output
doesn't copy — it mutates in place (append to a list, flip a flag
on a dataclass, etc.).  Keeping the store a pile of mutable
dataclasses is a deliberate contract — it's what lets augmenting
ops (:class:`~kiln.operations.auth.Auth`, the list modifiers) stay
small.

Thread-safety
-------------

None.  The engine is single-threaded; don't share a store across
threads.
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

    Outputs are keyed by ``(instance_id, op_name)``.  Ancestry
    between instances is tracked separately so tree walks don't
    have to parse dot-path ids.

    Query methods at a glance
    -------------------------

    **Scope-instance lookup** (return config objects, not outputs):

    - :meth:`ancestor_of` — walk up to find an enclosing scope's
      config instance.
    - :meth:`ancestor_id_of` — same walk, but return the id.
      Useful when you need the id to pass into the output-query
      methods below.
    - :meth:`children` — direct children of an instance, optionally
      filtered by child scope.
    - :meth:`scope_of` — resolve an id's :class:`~foundry.scope.Scope`.

    **Output lookup** (return objects emitted by ops):

    - :meth:`outputs_under` — every output of a type at or below a
      given instance id.  Good for aggregate passes (Auth at
      resource scope sweeping handlers).
    - :meth:`outputs_under_ancestor` — walk up to a named scope
      first, then collect.  Good for ops that need to reach sideways
      via a shared ancestor.
    - :meth:`output_under_ancestor` — singular form; raises if
      nothing matches.  For ops that expect exactly one target
      (e.g. a modifier op finding its parent op's
      :class:`~kiln.operations.list.ListResult`).
    - :meth:`entries` — raw ``(instance_id, op_name, items)`` tuples.
      The assembler uses this; ops rarely need to.

    **Mutation:**

    - :meth:`add` — engine calls this with an op's yielded outputs.
      Ops normally don't call it directly.
    - :meth:`register_instance` — engine calls this before invoking
      ``build()`` at a scope instance.  Ops never call it.

    Typical extension recipes
    -------------------------

    *Read an ancestor's config* (e.g. resource model from operation
    scope)::

        resource = ctx.store.ancestor_of(ctx.instance_id, "resource")

    *Augment every handler in your subtree* (e.g. Auth)::

        for handler in ctx.store.outputs_under(
            ctx.instance_id, RouteHandler
        ):
            handler.extra_deps.append(...)

    *Reach a specific output your parent scope produced* (e.g. a
    modifier finding its parent op's bundle)::

        bundle = ctx.store.output_under_ancestor(
            ctx.instance_id, "operation", ListResult
        )
        bundle.search_request.body_context["has_filter"] = True

    Attributes:
        scope_tree: :class:`~foundry.scope.ScopeTree` for the build's config.
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
        _parent_of: Map from an instance id to its parent id;
            drives :meth:`ancestor_of` / :meth:`ancestor_id_of`.

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
        """Resolve the :class:`~foundry.scope.Scope` of *instance_id*."""
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
        ancestor_id = self.ancestor_id_of(instance_id, scope_name)

        if ancestor_id is None:
            return None

        return self._instances.get(ancestor_id)

    def ancestor_id_of(
        self,
        instance_id: str,
        scope_name: str,
    ) -> str | None:
        """Return the enclosing instance id at *scope_name*, if any.

        Mirrors :meth:`ancestor_of` but returns the ancestor's id
        instead of its instance.  Ops that need to scan outputs
        under a higher scope use this to get the id
        :meth:`outputs_under` wants.
        """
        current = self._parent_of.get(instance_id)

        while current is not None:
            if self.scope_of(current).name == scope_name:
                return current

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
        returned (requires :attr:`scope_tree` to be populated).

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

    def outputs_under_ancestor[T](
        self,
        instance_id: str,
        scope_name: str,
        output_type: type[T],
    ) -> list[T]:
        """Return outputs under the ancestor of *instance_id* at *scope_name*.

        Convenience for the common "walk up to a named scope, then
        look for outputs there" pattern — used by ops that need to
        reach outputs an ancestor (or sibling via a shared ancestor)
        produced.  Returns ``[]`` when no ancestor at that scope is
        registered.
        """
        ancestor_id = self.ancestor_id_of(instance_id, scope_name)

        if ancestor_id is None:
            return []

        return self.outputs_under(ancestor_id, output_type)

    def output_under_ancestor[T](
        self,
        instance_id: str,
        scope_name: str,
        output_type: type[T],
    ) -> T:
        """Return the sole *output_type* output under the named ancestor.

        Singular form of :meth:`outputs_under_ancestor` — raises
        :class:`LookupError` when no ancestor is registered at
        *scope_name* or when the ancestor produced no output of
        *output_type*.  Returns the first match when more than one
        exists; callers that care about multiplicity should use the
        plural form.
        """
        results = self.outputs_under_ancestor(
            instance_id, scope_name, output_type
        )

        if not results:
            type_name = getattr(output_type, "__name__", repr(output_type))
            msg = (
                f"No {type_name} reachable from ancestor at scope "
                f"'{scope_name}' of '{instance_id}'."
            )
            raise LookupError(msg)

        return results[0]

    def entries(
        self,
    ) -> Iterator[tuple[str, str, list[object]]]:
        """Iterate stored entries as ``(instance_id, op_name, items)``.

        Used by the assembler to walk the store and dispatch each
        item to the correct renderer.
        """
        for (instance_id, op_name), items in self._items.items():
            yield instance_id, op_name, items
