"""Build engine: scope walking, operation execution, rendering.

The engine is the central orchestrator.  Given a config model,
a set of operations, and a render registry, it:

1. Discovers scopes from the config model's fields.
2. Groups operations by scope.
3. Topologically sorts operations within each scope.
4. Walks the config tree recursively, invoking each operation's
   ``build`` method at the appropriate scope level.
5. Collects output into a :class:`~foundry.store.BuildStore`.
6. Returns the store for a downstream assembler to render.

The engine does *not* render output to files -- that belongs
to framework-specific assemblers in the ``kiln`` package.
"""

from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel

from foundry.operation import (
    EmptyOptions,
    OperationEntry,
    OperationRegistry,
    load_default_registry,
)
from foundry.scope import PROJECT, Scope, ScopeTree, discover_scopes
from foundry.store import BuildStore


@dataclass
class BuildContext[InstanceT, ConfigT: BaseModel]:
    """Context passed to every operation's ``build`` method.

    Parameterized on two types:

    * ``InstanceT`` -- the scope's per-instance config (e.g.
      ``ResourceConfig`` for resource-scope ops).
    * ``ConfigT`` -- the project root config.  The engine itself
      uses ``BuildContext[Any, BaseModel]`` since it's
      target-agnostic; target-specific ops (e.g. all of kiln)
      annotate ``BuildContext[X, ProjectConfig]`` and get typed
      access to ``ctx.config.*`` without casting.

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

    config: ConfigT
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
            An :class:`~foundry.store.BuildStore` containing
            all objects produced by operations.

        """
        scope_tree = discover_scopes(type(config))
        self.registry.validate_scopes({scope.name for scope in scope_tree})

        state = _WalkState(
            config=config,
            store=BuildStore(scope_tree=scope_tree),
            ops=self.registry.sorted_by_scope(),
            scope_tree=scope_tree,
            package_prefix=self.package_prefix,
        )

        # Bootstrap the project root; every descendant flows
        # through _visit recursively.
        _visit(
            scope=PROJECT,
            instance_config=config,
            instance_id="project",
            parent_id=None,
            state=state,
        )

        return state.store


@dataclass
class _WalkState:
    """Constants threaded through every recursive :func:`_visit`."""

    config: BaseModel
    store: BuildStore
    ops: dict[str, list[OperationEntry]]
    scope_tree: ScopeTree
    package_prefix: str


def _visit(
    scope: Scope,
    instance_config: BaseModel,
    instance_id: str,
    parent_id: str | None,
    state: _WalkState,
) -> None:
    """Register one scope instance and recurse into its child scopes.

    Instance IDs are dot-joined paths that mirror the config
    structure — ``"project"`` at the root, then ``config_key.index``
    segments for each descendant level.  A resource at index 2
    under app at index 0 lands at
    ``"project.apps.0.resources.2"``, so the scope tree is
    recoverable from the id by matching each config_key back to
    its :class:`~foundry.scope.Scope`.

    Args:
        scope: The scope this instance belongs to.
        instance_config: The pydantic config object for this instance.
        instance_id: Pre-compounded dot-path id for this instance.
        parent_id: Id of the enclosing scope instance, or ``None``
            for the project root.
        state: Walk state shared across the recursion.

    """
    state.store.register_instance(
        instance_id,
        instance_config,
        parent=parent_id,
    )

    ctx = BuildContext(
        config=state.config,
        scope=scope,
        instance=instance_config,
        instance_id=instance_id,
        store=state.store,
        package_prefix=state.package_prefix,
    )

    ops = state.ops.get(scope.name, [])
    _run_ops(ops, ctx, after_children=False)

    for child_scope in state.scope_tree.children_of(scope):
        for own_id, child_config in _configs_for_scope(
            scope=child_scope,
            parent_config=instance_config,
        ):
            _visit(
                scope=child_scope,
                instance_config=child_config,
                instance_id=f"{instance_id}.{own_id}",
                parent_id=instance_id,
                state=state,
            )

    _run_ops(ops, ctx, after_children=True)


def _run_ops(
    ops: list[OperationEntry],
    ctx: BuildContext[Any, BaseModel],
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
    for meta, op_cls in ops:
        if meta.after_children != after_children:
            continue

        # dispatch_on: fire only on the instance whose discriminator
        # matches this op's name (e.g. OperationConfig.name == "get").
        if (
            meta.dispatch_on is not None
            and getattr(ctx.instance, meta.dispatch_on, None) != meta.name
        ):
            continue

        operation_instance = op_cls()
        when_method = getattr(operation_instance, "when", None)
        if callable(when_method) and not when_method(ctx):
            continue

        options = _resolve_options(op_cls, ctx.instance)

        ctx.store.add(
            ctx.instance_id,
            meta.name,
            *operation_instance.build(ctx, options),
        )


def _configs_for_scope(
    *,
    scope: Scope,
    parent_config: BaseModel,
) -> list[tuple[str, BaseModel]]:
    """Yield ``(own_id, scope_config)`` pairs for child *scope*.

    Walks :attr:`Scope.resolve_path` from *parent_config* to the
    scoped ``list[BaseModel]`` field and emits one entry per item.
    Never called with the project scope — :func:`_visit` handles
    that root case directly.

    Args:
        scope: The scope to enumerate.
        parent_config: The enclosing scope's config from which
            to resolve this scope's items.

    Returns:
        List of ``(own_id, scope_config)`` tuples, where
        ``own_id`` is the ``"{config_key}.{index}"`` segment that
        :func:`_visit` compounds onto the parent's id.

    """
    attr_value: object = parent_config
    for attr in scope.resolve_path:
        attr_value = getattr(attr_value, attr)

    scope_configs = cast("list[BaseModel]", attr_value)

    return [
        (f"{scope.config_key}.{index}", scope_config)
        for index, scope_config in enumerate(scope_configs)
    ]


def _resolve_options(op_cls: type, instance: object) -> BaseModel:
    """Build the Options model for an operation.

    If the instance exposes an ``options`` dict (as
    :class:`~kiln.config.schema.OperationConfig` does via
    ``model_extra``), those keys populate the Options model.
    Otherwise a default-constructed Options is returned.

    Args:
        op_cls: The operation class.
        instance: The config instance at the op's scope.

    Returns:
        A populated Options model.

    """
    options_cls = getattr(op_cls, "Options", None)
    if options_cls is None:
        return EmptyOptions()

    if isinstance(instance, BaseModel):
        raw = getattr(instance, "options", None)
        if isinstance(raw, dict):
            return options_cls(**raw)

    return options_cls()
