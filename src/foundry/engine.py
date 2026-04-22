"""Build engine: scope walking, operation execution, rendering.

The engine is the central orchestrator.  Given a config model,
a set of operations, and a render registry, it:

1. Discovers scopes from the config model's fields.
2. Groups operations by scope.
3. Topologically sorts operations within each scope.
4. Walks the config tree recursively, invoking each operation's
   ``build`` method at the appropriate scope level.
5. Collects output into a :class:`~foundry.render.BuildStore`.
6. Returns the store for a downstream assembler to render.

The engine does *not* render output to files -- that belongs
to framework-specific assemblers in the ``kiln`` package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NamedTuple, cast

from pydantic import BaseModel

from foundry.operation import (
    EmptyOptions,
    OperationEntry,
    OperationMeta,
    OperationRegistry,
    load_default_registry,
)
from foundry.render import BuildStore
from foundry.scope import PROJECT, Scope, discover_scopes


@dataclass
class BuildContext[InstanceT]:
    """Context passed to every operation's ``build`` method.

    Parameterized on the scope instance type so operations can
    annotate e.g. ``ctx: BuildContext[ResourceConfig]`` and get
    typed access to ``ctx.instance.*``.  The engine itself
    builds ``BuildContext[Any]`` since it's scope-agnostic.

    Attributes:
        config: The full project config (top-level model).
        scope: The scope this operation is running in.
        instance: The config object for the current scope
            instance (e.g. one resource's config dict).
        instance_id: Human-readable identifier for the
            instance within its scope.
        store: The build store for querying earlier operations'
            output.
        package_prefix: Dotted prefix for generated imports
            (e.g. ``"_generated"``).  Extensions use this to
            resolve their own import paths.

    """

    config: BaseModel
    scope: Scope
    instance: InstanceT
    instance_id: str
    store: BuildStore
    package_prefix: str = ""


@dataclass
class Engine:
    """Orchestrates the build phase of code generation.

    Attributes:
        registry: :class:`~foundry.operation.OperationRegistry`
            holding the ops to run.  Defaults to the populated
            :data:`~foundry.operation.DEFAULT_REGISTRY`; tests
            pass an isolated registry to keep their ops out of
            the global one.
        package_prefix: Dotted prefix for generated imports,
            forwarded to every :class:`BuildContext`.

    """

    registry: OperationRegistry = field(default_factory=load_default_registry)
    package_prefix: str = ""

    def build(self, config: BaseModel) -> BuildStore:
        """Run the build phase over all scopes and operations.

        Walks the scope tree depth-first.  At each scope
        instance, pre-phase operations (``after_children=False``)
        run before descending into children; post-phase
        operations (``after_children=True``) run after every
        child scope instance completes, so they can aggregate
        earlier output from the store.

        Args:
            config: The project config model instance.

        Returns:
            An :class:`~foundry.render.BuildStore` containing
            all objects produced by operations.

        """
        scopes = discover_scopes(type(config))
        self.registry.validate_scopes({scope.name for scope in scopes})

        state = _WalkState(
            config=config,
            store=BuildStore(scopes=scopes),
            ops=self.registry.sorted_by_scope(),
            scopes=scopes,
            package_prefix=self.package_prefix,
        )

        _visit(PROJECT, config, state, _ROOT_ANCESTRY)

        return state.store


@dataclass
class _WalkState:
    """Constants threaded through every recursive :func:`_visit`."""

    config: BaseModel
    store: BuildStore
    ops: dict[str, list[OperationEntry]]
    scopes: list[Scope]
    package_prefix: str


class _Ancestry(NamedTuple):
    """Position of the current recursion in the scope tree.

    Attributes:
        path: Compounded instance-id path from the project root,
            e.g. ``"project.apps.0"``.  Empty string only at the
            very root before the project scope is visited.  Joined
            with the current ``own_id`` (via ``.``) to form the
            full ``instance_id`` — which in turn becomes the
            enclosing path for the scope's children.
        parent_id: Id of the enclosing scope instance, forwarded
            to :meth:`BuildStore.register_instance` so
            :meth:`BuildStore.children` surfaces the tree.  ``None``
            before the project scope is visited.

    """

    path: str
    parent_id: str | None


_ROOT_ANCESTRY = _Ancestry(path="", parent_id=None)


def _visit(
    scope: Scope,
    config: BaseModel,
    state: _WalkState,
    ancestry: _Ancestry,
) -> None:
    """Recursively walk *scope* and its descendants.

    Instance IDs are dot-joined paths that mirror the config
    structure — root, then ``config_key.index`` pairs for each
    level.  A resource at index 2 under app at index 0 lands at
    ``"project.apps.0.resources.2"``.  The scope tree is
    recoverable from the id by matching each config_key back to
    its :class:`~foundry.scope.Scope`.
    """
    ops = state.ops.get(scope.name, [])
    for own_id, scope_config in _resolve_instances(scope, config):
        instance_id = f"{ancestry.path}.{own_id}" if ancestry.path else own_id
        state.store.register_instance(
            instance_id,
            scope_config,
            parent=ancestry.parent_id,
        )

        ctx = BuildContext(
            config=state.config,
            scope=scope,
            instance=scope_config,
            instance_id=instance_id,
            store=state.store,
            package_prefix=state.package_prefix,
        )

        _run_ops(ops, ctx, after_children=False)

        child_ancestry = _Ancestry(path=instance_id, parent_id=instance_id)
        for child_scope in state.scopes:
            if child_scope.parent is scope:
                _visit(child_scope, scope_config, state, child_ancestry)

        _run_ops(ops, ctx, after_children=True)


def _run_ops(
    ops: list[OperationEntry],
    ctx: BuildContext[Any],
    *,
    after_children: bool,
) -> None:
    """Execute the matching phase of operations for one scope instance.

    Args:
        ops: Sorted ``(meta, cls)`` entries for the scope (both
            phases).
        ctx: Build context for this scope instance.
        after_children: When ``True``, run only post-phase ops
            (``meta.after_children=True``); when ``False``, run
            only pre-phase ops.

    """
    allowed = _allowed_ops(ctx.instance)
    for meta, op_cls in ops:
        if meta.after_children != after_children:
            continue
        operation_instance = op_cls()
        when_method = getattr(operation_instance, "when", None)
        has_when = callable(when_method)
        # An instance's ``operations`` list gates user-facing ops.
        # Cross-cutting ops that define ``when`` opt-in at runtime
        # and bypass the explicit list.
        if not has_when and allowed is not None and meta.name not in allowed:
            continue
        if has_when and when_method is not None and not when_method(ctx):
            continue
        options = _resolve_options(meta, op_cls, ctx.instance)
        ctx.store.add(
            ctx.instance_id,
            meta.name,
            *operation_instance.build(ctx, options),
        )


def _resolve_instances(
    scope: Scope,
    config: BaseModel,
) -> list[tuple[str, BaseModel]]:
    """Yield ``(instance_id, scope_config)`` pairs for *scope*.

    The root (project) scope always returns a single entry with
    the config itself.  Child scopes walk
    :attr:`Scope.resolve_path` from *config* to locate the list of
    items, then emit one entry per item.

    Args:
        scope: The scope to enumerate.
        config: The enclosing scope's config instance from which
            to resolve this scope's items.

    Returns:
        List of ``(id, scope_config)`` tuples.

    """
    if scope is PROJECT:
        return [("project", config)]

    attr_value: object = config
    for attr in scope.resolve_path:
        attr_value = getattr(attr_value, attr)
    scope_configs = cast("list[BaseModel]", attr_value)

    # Own-id segment is ``{config_key}.{index}`` — paired so the
    # full compounded instance id tracks config structure (e.g.
    # ``"project.apps.0.resources.2"``).  Ops that need a human
    # identifier derive it from the scope instance directly
    # rather than parsing this id.
    return [
        (f"{scope.config_key}.{index}", scope_config)
        for index, scope_config in enumerate(scope_configs)
    ]


def _resolve_options(
    meta: OperationMeta,
    op_cls: type,
    instance: object,
) -> BaseModel:
    """Build the Options model for an operation.

    If the instance is a Pydantic model with an ``options``
    field matching the operation's Options class, use that.
    Otherwise, return a default-constructed Options.

    Args:
        meta: Operation metadata (used for ``meta.name`` lookups
            in the instance's ``operations`` list).
        op_cls: The operation class.
        instance: The config instance for this scope.

    Returns:
        A populated Options model.

    """
    options_cls = getattr(op_cls, "Options", None)
    if options_cls is None:
        return EmptyOptions()

    # Check if instance has an options dict/field we can use.
    if isinstance(instance, BaseModel):
        raw = getattr(instance, "options", None)
        if isinstance(raw, dict):
            return options_cls(**raw)

    # Check instance.operations for a matching entry with options.
    raw = _find_op_options(instance, meta.name)
    if raw:
        return options_cls(**raw)

    return options_cls()


def _find_op_options(
    instance: object,
    op_name: str,
) -> dict[str, object] | None:
    """Find options for a named operation in the instance's list.

    Checks ``instance.operations`` for an entry whose ``name``
    matches *op_name* and returns its ``options`` dict.

    Args:
        instance: The config instance for one scope item.
        op_name: The operation name to find.

    Returns:
        Options dict, or ``None`` if not found.

    """
    ops = getattr(instance, "operations", None)
    if ops is None:
        return None
    for entry in ops:
        if isinstance(entry, str):
            continue
        name = getattr(entry, "name", None)
        if name == op_name:
            options = getattr(entry, "options", None)
            if isinstance(options, dict):
                return options
    return None


def _allowed_ops(instance: object) -> set[str] | None:
    """Extract allowed operation names from a scope instance.

    If the instance has an ``operations`` field (a list of
    strings or objects with a ``name`` attribute), returns the
    set of operation names.  Otherwise returns ``None`` meaning
    all operations are allowed.

    Args:
        instance: The config instance for one scope item.

    Returns:
        Set of operation names, or ``None`` if unrestricted.

    """
    ops = getattr(instance, "operations", None)
    if ops is None:
        return None
    names: set[str] = set()
    for entry in ops:
        if isinstance(entry, str):
            names.add(entry)
        else:
            name = getattr(entry, "name", None)
            if name:
                names.add(name)
    return names
