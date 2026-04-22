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
from pathlib import PurePosixPath
from typing import Any, cast

from pydantic import BaseModel

from foundry.operation import (
    EmptyOptions,
    discover_operations,
    get_operation_meta,
    topological_sort,
)
from foundry.render import BuildStore, InstanceKey
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
        operations: Operation classes decorated with
            ``@operation``.  Defaults to every class registered
            under the ``foundry.operations`` entry-point group
            (see :func:`~foundry.operation.discover_operations`),
            so production callers just write ``Engine()``.  Tests
            override this to run a curated subset.
        scopes: Discovered scopes (auto-populated from
            config if not provided).
        package_prefix: Dotted prefix for generated imports,
            forwarded to every :class:`BuildContext`.

    """

    operations: list[type] = field(default_factory=discover_operations)
    scopes: list[Scope] = field(default_factory=list)
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
        if not self.scopes:
            self.scopes = discover_scopes(type(config))

        _validate_ops(self.operations, self.scopes)

        state = _WalkState(
            config=config,
            store=BuildStore(),
            ops=_sort_by_scope(self.operations, self.scopes),
            scopes=self.scopes,
            package_prefix=self.package_prefix,
        )

        self._visit(PROJECT, config, state, PurePosixPath(), None)

        return state.store

    def _visit(
        self,
        scope: Scope,
        parent_instance: object,
        state: _WalkState,
        parent_instance_id: PurePosixPath,
        parent_key: InstanceKey | None,
    ) -> None:
        """Recursively walk *scope* and its descendants.

        Instance IDs are compounded with ``/`` across non-root
        ancestors so sibling scope trees can't collide on a bare
        base ID.  For example, an ``article`` resource nested
        under the ``blog`` app lands in the store under
        ``("resource", "blog/article")``.  The project scope is
        excluded from the prefix — its ID ``"project"`` would add
        noise to every descendant without disambiguating anything.

        The parent ``(scope, id)`` is also recorded on the store
        so aggregator ops can query descendants without
        reconstructing store keys.

        Args:
            scope: The scope currently being walked.
            parent_instance: The instance from which to resolve
                this scope's items (for the root, this is the
                project config itself).
            state: Shared walk state — config, store, op groups,
                and the child-scope index.
            parent_instance_id: The compounded instance ID path
                of the enclosing scope, or an empty path when the
                parent is the project root (no prefixing).
            parent_key: Store key of the enclosing scope instance,
                or ``None`` at the project root.  Forwarded to
                :meth:`BuildStore.register_instance` so
                :meth:`BuildStore.children` surfaces the tree.

        """
        for own_id, inst_obj in _resolve_instances(scope, parent_instance):
            instance_path = parent_instance_id / own_id
            instance_id = str(instance_path)
            own_key: InstanceKey = (scope.name, instance_id)
            state.store.register_instance(
                scope.name,
                instance_id,
                inst_obj,
                parent=parent_key,
            )
            ctx = BuildContext(
                config=state.config,
                scope=scope,
                instance=inst_obj,
                instance_id=instance_id,
                store=state.store,
                package_prefix=state.package_prefix,
            )
            ops = state.ops.get(scope.name, [])
            _run_ops(ops, ctx, after_children=False)
            next_parent = PurePosixPath() if scope is PROJECT else instance_path
            for child in state.scopes:
                if child.parent is scope:
                    self._visit(
                        child,
                        inst_obj,
                        state,
                        next_parent,
                        own_key,
                    )
            _run_ops(ops, ctx, after_children=True)


@dataclass
class _WalkState:
    """Constants threaded through every recursive :meth:`Engine._visit`."""

    config: BaseModel
    store: BuildStore
    ops: dict[str, list[type]]
    scopes: list[Scope]
    package_prefix: str


def _validate_ops(operations: list[type], scopes: list[Scope]) -> None:
    """Raise if any operation has missing or unknown metadata.

    Args:
        operations: Operation classes to validate.
        scopes: Discovered scopes.

    Raises:
        ValueError: If an operation has no metadata or targets
            an unknown scope.

    """
    names = {scope.name for scope in scopes}

    for operation_cls in operations:
        meta = get_operation_meta(operation_cls)

        if meta.scope not in names:
            msg = (
                f"Operation '{meta.name}' targets "
                f"scope '{meta.scope}' which was not "
                f"discovered from the config"
            )

            raise ValueError(msg)


def _sort_by_scope(
    operations: list[type],
    scopes: list[Scope],
) -> dict[str, list[type]]:
    """Group operations by scope and topologically sort each group.

    Phase (pre vs post) is encoded on the op's metadata via
    ``after_children`` and split out at runtime in
    :func:`_run_ops` — a single sorted list per scope is enough.

    Args:
        operations: All operation classes.
        scopes: Discovered scopes.

    Returns:
        Map from scope name to topo-sorted operation classes.

    """
    return {
        scope.name: topological_sort(
            [
                operation
                for operation in operations
                if get_operation_meta(operation).scope == scope.name
            ]
        )
        for scope in scopes
    }


def _run_ops(
    ops: list[type],
    ctx: BuildContext[Any],
    *,
    after_children: bool,
) -> None:
    """Execute the matching phase of operations for one scope instance.

    Args:
        ops: Sorted operation classes for the scope (both phases).
        ctx: Build context for this scope instance.
        after_children: When ``True``, run only post-phase ops
            (``meta.after_children=True``); when ``False``, run
            only pre-phase ops.

    """
    allowed = _allowed_ops(ctx.instance)
    for op_cls in ops:
        meta = get_operation_meta(op_cls)
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
        options = _resolve_options(op_cls, ctx.instance)
        ctx.store.add(
            ctx.scope.name,
            ctx.instance_id,
            meta.name,
            *operation_instance.build(ctx, options),
        )


def _resolve_instances(
    scope: Scope,
    parent_instance: object,
) -> list[tuple[str, object]]:
    """Yield ``(instance_id, instance_object)`` pairs for *scope*.

    The root (project) scope always returns a single entry with
    the parent instance itself.  Child scopes walk
    :attr:`Scope.resolve_path` from *parent_instance* to locate
    the list of items, then emit one entry per item.

    Args:
        scope: The scope to enumerate.
        parent_instance: The parent scope instance from which
            to resolve this scope's items.

    Returns:
        List of ``(id, object)`` tuples.

    """
    if scope is PROJECT:
        return [("project", parent_instance)]

    for attr in scope.resolve_path:
        parent_instance = getattr(parent_instance, attr)

    items = cast("list[object]", parent_instance)

    return [
        (_instance_id(scope.name, index), item)
        for index, item in enumerate(items)
    ]


def _instance_id(scope_name: str, index: int) -> str:
    """Derive an opaque, stable instance ID for a scope item.

    Ids are ``{scope_name}-{index}`` — deterministic given config
    order and deliberately unrelated to any config field.  Ops
    that need a human identifier (module slug, function name,
    test description) must derive it from the scope instance
    directly (e.g. ``resource.model``) rather than parsing this
    id.

    Args:
        scope_name: Name of the scope.
        index: Position in the scope's list.

    Returns:
        Instance identifier string of the form
        ``"{scope_name}-{index}"``.

    """
    return f"{scope_name}-{index}"


def _resolve_options(
    op_cls: type,
    instance: object,
) -> BaseModel:
    """Build the Options model for an operation.

    If the instance is a Pydantic model with an ``options``
    field matching the operation's Options class, use that.
    Otherwise, return a default-constructed Options.

    Args:
        op_cls: The operation class.
        instance: The config instance for this scope.

    Returns:
        A populated Options model.

    """
    meta = get_operation_meta(op_cls)
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
