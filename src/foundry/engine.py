"""Build engine: scope walking, operation execution, rendering.

The engine is the central orchestrator.  Given a config model,
a set of operations, and a render registry, it:

1. Discovers scopes from the config model's fields.
2. Groups operations by scope.
3. Topologically sorts operations within each scope.
4. Walks the config tree, invoking each operation's ``build``
   method at the appropriate scope level.
5. Collects output into a :class:`~foundry.render.BuildStore`.
6. Returns the store for a downstream assembler to render.

The engine does *not* render output to files -- that
belongs to framework-specific assemblers in the ``kiln``
package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from foundry.operation import (
    EmptyOptions,
    get_operation_meta,
    topological_sort,
)
from foundry.render import BuildStore
from foundry.scope import PROJECT, Scope, discover_scopes


@dataclass
class BuildContext:
    """Context passed to every operation's ``build`` method.

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
    instance: Any
    instance_id: str
    store: BuildStore
    package_prefix: str = ""


@dataclass
class Engine:
    """Orchestrates the build phase of code generation.

    Attributes:
        operations: Operation classes decorated with
            ``@operation``.
        scopes: Discovered scopes (auto-populated from
            config if not provided).
        package_prefix: Dotted prefix for generated imports,
            forwarded to every :class:`BuildContext`.

    """

    operations: list[type] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=list)
    package_prefix: str = ""

    def build(self, config: BaseModel) -> BuildStore:
        """Run the build phase over all scopes and operations.

        Args:
            config: The project config model instance.

        Returns:
            An :class:`~foundry.render.BuildStore` containing
            all objects produced by operations.

        """
        if not self.scopes:
            self.scopes = discover_scopes(type(config))

        store = BuildStore()
        sorted_ops = _group_and_sort(self.operations, self.scopes)

        for scope in self.scopes:
            ops = sorted_ops.get(scope.name, [])
            if not ops:
                continue
            instances = _scope_instances(config, scope)
            for inst_id, inst_obj in instances:
                ctx = BuildContext(
                    config=config,
                    scope=scope,
                    instance=inst_obj,
                    instance_id=inst_id,
                    store=store,
                    package_prefix=self.package_prefix,
                )
                _run_ops(ops, ctx)

        return store


def _group_and_sort(
    operations: list[type],
    scopes: list[Scope],
) -> dict[str, list[type]]:
    """Group operations by scope and topologically sort each group.

    Args:
        operations: All operation classes.
        scopes: Discovered scopes.

    Returns:
        Mapping from scope name to sorted operation list.

    Raises:
        ValueError: If an operation has no metadata or targets
            an unknown scope.

    """
    by_scope: dict[str, list[type]] = {s.name: [] for s in scopes}
    for op_cls in operations:
        meta = get_operation_meta(op_cls)
        if meta is None:
            msg = f"{op_cls} has no @operation metadata"
            raise ValueError(msg)
        if meta.scope not in by_scope:
            msg = (
                f"Operation '{meta.name}' targets "
                f"scope '{meta.scope}' which was not "
                f"discovered from the config"
            )
            raise ValueError(msg)
        by_scope[meta.scope].append(op_cls)

    return {
        name: topological_sort(ops) if ops else []
        for name, ops in by_scope.items()
    }


def _run_ops(
    ops: list[type],
    ctx: BuildContext,
) -> None:
    """Execute operations for one scope instance.

    Args:
        ops: Sorted operation classes.
        ctx: Build context for this scope instance.

    """
    allowed = _allowed_ops(ctx.instance)
    for op_cls in ops:
        meta = get_operation_meta(op_cls)
        if meta is None:  # pragma: no cover
            continue
        op = op_cls()
        when_fn = getattr(op, "when", None)
        has_when = callable(when_fn)
        # An instance's ``operations`` list gates user-facing ops.
        # Cross-cutting ops that define ``when`` opt-in at runtime
        # and bypass the explicit list.
        if not has_when and allowed is not None and meta.name not in allowed:
            continue
        if has_when and when_fn is not None and not when_fn(ctx):
            continue
        options = _resolve_options(op_cls, ctx.instance)
        ctx.store.add(
            ctx.scope.name,
            ctx.instance_id,
            meta.name,
            *op.build(ctx, options),
        )


def _scope_instances(
    config: BaseModel,
    scope: Scope,
) -> list[tuple[str, Any]]:
    """Yield ``(instance_id, instance_object)`` pairs for a scope.

    For the project scope, returns a single entry with the
    full config.  For child scopes, iterates the list field
    indicated by ``scope.config_key``.

    Args:
        config: The top-level config model.
        scope: The scope to enumerate.

    Returns:
        List of ``(id, object)`` tuples.

    """
    if scope is PROJECT:
        return [("project", config)]

    items = getattr(config, scope.config_key, [])
    result: list[tuple[str, Any]] = []
    for i, item in enumerate(items):
        inst_id = _instance_id(item, scope.name, i)
        result.append((inst_id, item))
    return result


def _instance_id(
    item: object,
    scope_name: str,
    index: int,
) -> str:
    """Derive a human-readable instance ID for a scope item.

    Checks ``name`` first, then extracts the class name from
    a dotted ``model`` path, then falls back to
    ``{scope}_{index}``.

    Args:
        item: The config instance.
        scope_name: Name of the scope (for fallback).
        index: Position in the list (for fallback).

    Returns:
        Instance identifier string.

    """
    name = getattr(item, "name", None)
    if name:
        return name
    model = getattr(item, "model", None)
    if model and isinstance(model, str):
        _, _, class_name = model.rpartition(".")
        return class_name.lower()
    return f"{scope_name}_{index}"


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
    if meta is not None:
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
