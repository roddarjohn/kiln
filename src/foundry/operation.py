"""Operation protocol and ``@operation`` decorator.

An operation is a unit of generation that declares its scope,
dependencies, and an ``Options`` model for configuration
validation.  The ``@operation`` decorator attaches metadata
to the class so the engine can discover and wire it.
"""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from pydantic import BaseModel

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

    """

    name: str
    scope: str
    requires: tuple[str, ...] = ()
    after_children: bool = False


# -------------------------------------------------------------------
# Default options
# -------------------------------------------------------------------


class EmptyOptions(BaseModel):
    """Default options model for operations with no config."""


# -------------------------------------------------------------------
# Decorator
# -------------------------------------------------------------------

_OPERATION_META_ATTR = "__operation_meta__"


def operation(
    name: str,
    *,
    scope: str,
    requires: list[str] | None = None,
    after_children: bool = False,
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
    inspecting :attr:`BuildContext.store` and mutating the
    objects returned by :meth:`BuildStore.get_by_type` or
    friends in place.  Combined with ``requires`` for ordering
    and ``when`` for activation, a single operation mechanism
    covers both "produce" and "augment" roles.

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

    Returns:
        Class decorator.

    Example::

        @operation("get", scope="resource")
        class Get:
            class Options(BaseModel):
                fields: list[FieldSpec] | None = None

            def build(self, ctx, options):
                return [RouteHandler(...)]

        @operation("auth", scope="resource", requires=["get"])
        class Auth:
            def when(self, ctx):
                return ctx.config.auth is not None

            def build(self, ctx, options):
                for h in ctx.store.get_by_type(RouteHandler):
                    h.extra_deps.append("...")
                return []

        @operation("router", scope="project", after_children=True)
        class Router:
            def build(self, ctx, options):
                handlers = ctx.store.get_by_type(RouteHandler)
                return [...]  # aggregate mounts from handlers

    """
    reqs = tuple(requires or [])

    def decorator(cls: type) -> type:
        meta = OperationMeta(
            name=name,
            scope=scope,
            requires=reqs,
            after_children=after_children,
        )
        setattr(cls, _OPERATION_META_ATTR, meta)
        if not hasattr(cls, "Options"):
            cls.Options = EmptyOptions
        return cls

    return decorator


def get_operation_meta(
    cls: type,
) -> OperationMeta | None:
    """Return the :class:`OperationMeta` for *cls*, or ``None``."""
    return getattr(cls, _OPERATION_META_ATTR, None)


# -------------------------------------------------------------------
# Topological sort
# -------------------------------------------------------------------


def topological_sort(
    operations: list[type],
) -> list[type]:
    """Sort operations by dependency order.

    Delegates to :class:`graphlib.TopologicalSorter` and breaks
    ties alphabetically so output is deterministic regardless of
    input ordering.  Raises :class:`ValueError` on cycles or
    missing dependencies.

    Args:
        operations: Operation classes with attached metadata.

    Returns:
        Operations in dependency-safe execution order.

    """
    meta_map: dict[str, OperationMeta] = {}
    cls_map: dict[str, type] = {}

    for cls in operations:
        meta = get_operation_meta(cls)
        if meta is None:
            msg = f"{cls} has no @operation metadata"
            raise ValueError(msg)
        meta_map[meta.name] = meta
        cls_map[meta.name] = cls

    sorter: TopologicalSorter[str] = TopologicalSorter()
    for name, meta in meta_map.items():
        sorter.add(name)
        for dependency in meta.requires:
            if dependency not in meta_map:
                msg = (
                    f"Operation '{name}' requires '{dependency}', "
                    f"which is not registered"
                )
                raise ValueError(msg)
            sorter.add(name, dependency)

    try:
        sorter.prepare()
    except CycleError as exc:
        msg = "Cycle detected in operation dependencies"
        raise ValueError(msg) from exc

    result: list[str] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        result.extend(ready)
        for name in ready:
            sorter.done(name)

    return [cls_map[name] for name in result]
