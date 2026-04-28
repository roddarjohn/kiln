"""Operation protocol, ``@operation`` decorator, and registry.

An operation is a unit of generation that declares its scope,
dependencies, and an ``Options`` model for configuration
validation.  The :func:`operation` decorator captures that
metadata and stashes it on the class as ``_operation_meta``;
:func:`load_registry` then walks a target's entry-point group
at build time, loads each declared class, and registers them
into a fresh :class:`OperationRegistry`.

There is no process-wide default registry.  Each
:class:`~foundry.target.Target` declares the entry-point group
its operations live under (e.g. ``"kiln.operations"`` for the
kiln target, ``"be_root.operations"`` for be_root); the
pipeline builds an isolated registry per build, so two targets
installed side-by-side can never see each other's ops.

Tests that want to exercise a specific op without going through
entry-point discovery can pass ``registry=<isolated>`` to
:func:`operation` and :class:`~foundry.engine.Engine` directly.
"""

import importlib.metadata
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import Any, NamedTuple

from pydantic import BaseModel

#: Class attribute the :func:`operation` decorator stashes meta
#: under, for :func:`load_registry` to read at entry-point time.
_META_ATTR = "_operation_meta"

# -------------------------------------------------------------------
# Metadata
# -------------------------------------------------------------------


@dataclass(frozen=True)
class OperationMeta:
    """Metadata attached to a decorated operation class.

    Attributes:
        name: Unique operation name (e.g. ``"get"``).
        scope: Scope name this operation runs in
            (e.g. ``"resource"``).
        requires: Names of operations that must run before
            this one within the same scope.
        after_children: When ``True``, this project-scope
            operation runs *after* all child scopes have
            executed, so its ``build`` method can inspect
            objects produced at the resource/app scopes
            via the build store.  Ignored outside the
            project scope (the engine raises if set).
        dispatch_on: Attribute name on ``ctx.instance`` whose
            value must equal :attr:`name` for this op to run.
            Engine skips the op silently when the attribute is
            absent or mismatched.  Use it at scopes where the
            instance is a discriminated union — every registered
            op at the scope shares the scope walk and each
            dispatches to its own entry by name.

    """

    name: str
    scope: str
    requires: tuple[str, ...] = ()
    after_children: bool = False
    dispatch_on: str | None = None


# -------------------------------------------------------------------
# Default options
# -------------------------------------------------------------------


class EmptyOptions(BaseModel):
    """Default options model for operations with no config."""


# -------------------------------------------------------------------
# Registry
# -------------------------------------------------------------------


class OperationEntry(NamedTuple):
    """Pre-resolved pair of metadata and operation class."""

    meta: OperationMeta
    cls: type


@dataclass
class OperationRegistry:
    """Collection of ``(meta, cls)`` entries with query helpers.

    Populated by the :func:`operation` decorator at decoration
    time.  The engine reads entries from the registry to walk
    scopes, group by scope, and topo-sort within a scope — it
    never needs to look up metadata on individual classes.

    Attributes:
        entries: ``(meta, cls)`` pairs, in registration order.

    """

    entries: list[OperationEntry] = field(default_factory=list)

    def register(self, meta: OperationMeta, cls: type) -> None:
        """Append an ``OperationEntry`` to :attr:`entries`."""
        self.entries.append(OperationEntry(meta=meta, cls=cls))

    def validate_scopes(self, known: set[str]) -> None:
        """Raise if any operation targets a scope outside *known*.

        Args:
            known: Set of scope names discovered from the config
                model.

        Raises:
            ValueError: If an op's declared scope is not in
                *known*.

        """
        for entry in self.entries:
            if entry.meta.scope not in known:
                msg = (
                    f"Operation '{entry.meta.name}' targets "
                    f"scope '{entry.meta.scope}' which was not "
                    f"discovered from the config"
                )

                raise ValueError(msg)

    def sorted_by_scope(self) -> dict[str, list[OperationEntry]]:
        """Group entries by scope and topo-sort each bucket.

        Phase (pre vs post) is encoded on ``meta.after_children``
        and split out at runtime by the engine; a single sorted
        list per scope is enough.  Scopes with no registered ops
        are omitted — the engine uses ``dict.get(scope, [])``.

        Returns:
            Mapping from scope name to topo-sorted
            ``(meta, cls)`` entries.

        """
        buckets: dict[str, list[OperationEntry]] = {}

        for entry in self.entries:
            buckets.setdefault(entry.meta.scope, []).append(entry)

        return {name: _topo_sort(ops) for name, ops in buckets.items()}


# -------------------------------------------------------------------
# Decorator
# -------------------------------------------------------------------


def operation(  # noqa: PLR0913
    name: str,
    *,
    scope: str,
    requires: list[str] | None = None,
    after_children: bool = False,
    dispatch_on: str | None = None,
    registry: OperationRegistry | None = None,
) -> Any:  # noqa: ANN401
    """Decorate a class as a kiln operation.

    The decorated class must define:

    - ``Options``: a :class:`pydantic.BaseModel` subclass
      (defaults to :class:`EmptyOptions` if absent).
    - ``build(self, ctx, options) -> list``: produces output
      objects for the engine to collect.

    Optionally it may define:

    - ``when(self, ctx) -> bool``: when present and returning
      ``False``, the engine skips this operation for the
      current build context.  Use this for conditional
      operations (e.g. auth, which only runs when the project
      has auth configured).

    Operations can also modify earlier operations' outputs by
    inspecting :attr:`~foundry.engine.BuildContext.store` and
    mutating the objects returned by
    :meth:`~foundry.store.BuildStore.outputs_under` in place.
    Combined with ``requires`` for ordering and ``when`` for
    activation, a single operation mechanism covers both
    "produce" and "augment" roles.

    The decorator stashes the captured :class:`OperationMeta` on
    the class under ``_operation_meta``;
    :func:`load_registry` reads it back at entry-point load
    time.  When *registry* is supplied (the test path) the
    decorator also pushes the entry into that registry directly,
    so unit tests can keep ops out of the entry-point flow
    entirely.

    Args:
        name: Unique operation name.
        scope: Scope name (e.g. ``"resource"``, ``"app"``,
            ``"project"``).
        requires: Operation names that must run first.
        after_children: When ``True`` (project scope only),
            defer this operation until every child scope has
            executed so ``build`` can walk child output in the
            store.  The engine rejects this flag at any other
            scope.
        dispatch_on: Attribute name on the scope instance to
            compare against *name*.  When set, the engine skips
            the op unless ``getattr(ctx.instance, dispatch_on)
            == name``.  Designed for scopes whose instance is a
            discriminated-union config (e.g. ``OperationConfig``
            entries under a resource), where multiple ops share
            one scope and each matches a single entry.
        registry: Optional registry to push into directly.
            ``None`` (the production path) means the decorator
            only stashes meta on the class; the pipeline picks
            it up later via :func:`load_registry`.  Tests pass
            an isolated registry to keep their ops out of the
            entry-point flow.

    Returns:
        Class decorator.

    Example::

        @operation("get", scope="resource")
        class Get:
            class Options(BaseModel):
                fields: list[FieldSpec] | None = None

            def build(self, ctx, options):
                return [RouteHandler(...)]

    """
    reqs = tuple(requires or [])
    meta = OperationMeta(
        name=name,
        scope=scope,
        requires=reqs,
        after_children=after_children,
        dispatch_on=dispatch_on,
    )

    def decorator(cls: type) -> type:
        setattr(cls, _META_ATTR, meta)

        if not hasattr(cls, "Options"):
            cls.Options = EmptyOptions

        if registry is not None:
            registry.register(meta, cls)

        return cls

    return decorator


def load_registry(entry_point_group: str) -> OperationRegistry:
    """Build a fresh :class:`OperationRegistry` from an entry-point group.

    Walks ``entry_point_group``, loads each declared class, and
    registers it via the :class:`OperationMeta` stashed on the
    class by the :func:`operation` decorator.  Each call returns
    a brand-new registry, so two targets sharing a process never
    see each other's ops.

    Args:
        entry_point_group: Dotted entry-point group name, e.g.
            ``"kiln.operations"`` or ``"be_root.operations"``.

    Returns:
        A populated :class:`OperationRegistry`.

    Raises:
        TypeError: If a discovered class is missing the
            ``_operation_meta`` attribute -- typically because
            the class wasn't decorated with
            :func:`operation`.

    """
    registry = OperationRegistry()

    for entry_point in importlib.metadata.entry_points(group=entry_point_group):
        cls = entry_point.load()
        meta = getattr(cls, _META_ATTR, None)

        if meta is None:
            msg = (
                f"Entry point {entry_point.name!r} in group "
                f"{entry_point_group!r} loaded {cls!r}, which is "
                f"not an @operation-decorated class"
            )
            raise TypeError(msg)

        registry.register(meta, cls)

    return registry


# -------------------------------------------------------------------
# Topological sort
# -------------------------------------------------------------------


def _topo_sort(entries: list[OperationEntry]) -> list[OperationEntry]:
    """Sort *entries* by dependency order.

    Delegates to :class:`graphlib.TopologicalSorter` and breaks
    ties alphabetically on operation name so output is
    deterministic regardless of input order.  Raises
    :class:`ValueError` on cycles or missing dependencies.
    """
    # Sorted by name so ties in the topo result are broken
    # deterministically — TopologicalSorter.static_order yields
    # siblings in insertion order.
    ordered = sorted(entries, key=lambda entry: entry.meta.name)

    graph: dict[str, set[str]] = {
        entry.meta.name: set(entry.meta.requires) for entry in ordered
    }

    _validate_requires(graph)

    by_name: dict[str, OperationEntry] = {
        entry.meta.name: entry for entry in ordered
    }

    try:
        return [
            by_name[name] for name in TopologicalSorter(graph).static_order()
        ]

    except CycleError as exc:
        msg = "Cycle detected in operation dependencies"
        raise ValueError(msg) from exc


def _validate_requires(graph: dict[str, set[str]]) -> None:
    """Raise if any declared dependency isn't a node in *graph*.

    ``requires`` references other ops by name and can only be
    checked once the full set of ops is known — which is also
    why this lives alongside topo-sort rather than at registration
    time (decorator order is import order, not dependency order).
    """
    for name, deps in graph.items():
        for dependency in deps:
            if dependency not in graph:
                msg = (
                    f"Operation '{name}' requires '{dependency}', "
                    f"which is not registered"
                )

                raise ValueError(msg)
