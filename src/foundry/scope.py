"""Scope discovery from Pydantic config models.

A scope represents a level in the config tree at which
operations can run.  Scopes are derived from config fields
that hold ``list[BaseModel]`` values — each item in the list
becomes one scope instance the engine iterates over.

Scopes form a tree rooted at :data:`PROJECT`.  A child scope's
instances are resolved from its parent scope instance by walking
:attr:`Scope.resolve_path` — a tuple of attribute names.  For a
direct list-of-models field this is just ``(field_name,)``; for
nested structures like ``AppRef.config.resources`` the path is
``("config", "resources")``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass(frozen=True)
class Scope:
    """A named level in the config tree.

    Attributes:
        name: Human-readable scope name, e.g. ``"resource"``.
        config_key: The config field name that produced this
            scope, e.g. ``"resources"``.  Empty string for the
            root (project) scope.
        parent: The parent scope, or ``None`` for the root.
        resolve_path: Dotted attribute path from ``parent``'s
            scope instance to this scope's list of items.  Empty
            for the root scope.  Defaults to ``(config_key,)``
            for a direct child.

    """

    name: str
    config_key: str
    parent: Scope | None = None
    resolve_path: tuple[str, ...] = field(default=())


# The root scope — always present.
PROJECT = Scope(name="project", config_key="")


def discover_scopes(
    config_cls: type[BaseModel],
) -> list[Scope]:
    """Derive scopes from a Pydantic model's fields, recursively.

    The top-level config is always the ``"project"`` scope.
    Each ``list[BaseModel]`` field becomes a child scope of the
    current level; the item type is then itself descended into
    to discover grandchild scopes.

    Non-list ``BaseModel`` fields (e.g. ``AppRef.config``) are
    traversed transparently: their nested ``list[BaseModel]``
    fields become scopes rooted at the enclosing level, with
    ``resolve_path`` reflecting the full attribute walk.

    Cycles (e.g. ``KilnConfig → AppRef → KilnConfig``) are
    detected via a visited-classes set so discovery always
    terminates.

    Args:
        config_cls: The Pydantic model class to inspect.

    Returns:
        Flat list of all discovered scopes, project first, in
        depth-first order of the config tree.

    """
    scopes: list[Scope] = [PROJECT]
    _discover(
        config_cls,
        parent=PROJECT,
        path_prefix=(),
        seen_lists=set(),
        out=scopes,
    )
    return scopes


def _discover(
    cls: type[BaseModel],
    *,
    parent: Scope,
    path_prefix: tuple[str, ...],
    seen_lists: set[type[BaseModel]],
    out: list[Scope],
) -> None:
    """Walk *cls*'s fields, appending discovered scopes to *out*.

    The same scope *name* may appear at multiple places in the
    tree (e.g. ``resource`` may live directly under the project
    *and* inside each ``app``).  That's intentional: operations
    dispatch by scope name, so they run at every matching node.

    Cycle detection tracks only classes reached via ``list``
    descent, because those are what can nest unboundedly.
    Non-list ``BaseModel`` fields (e.g. ``AppRef.config``) are
    traversed transparently so nested list fields surface as
    scopes with a compound ``resolve_path``.

    Args:
        cls: The Pydantic model class to inspect.
        parent: The scope whose instance hosts these fields.
        path_prefix: Attribute path accumulated so far from the
            parent scope instance to ``cls``.  Empty when
            ``cls`` is itself the parent scope's instance type.
        seen_lists: Item types already entered via ``list[...]``
            descent; used to break cycles without blocking the
            wrapper traversal above.
        out: Output list; mutated in place.

    """
    for name, info in cls.model_fields.items():
        ann = info.annotation
        inner = _list_item_type(ann)
        if inner is not None:
            if not (isinstance(inner, type) and issubclass(inner, BaseModel)):
                continue
            child = Scope(
                name=_singularize(name),
                config_key=name,
                parent=parent,
                resolve_path=(*path_prefix, name),
            )
            out.append(child)
            if inner not in seen_lists:
                _discover(
                    inner,
                    parent=child,
                    path_prefix=(),
                    seen_lists=seen_lists | {inner},
                    out=out,
                )
            continue

        if isinstance(ann, type) and issubclass(ann, BaseModel):
            _discover(
                ann,
                parent=parent,
                path_prefix=(*path_prefix, name),
                seen_lists=seen_lists,
                out=out,
            )


def _singularize(name: str) -> str:
    """Naive singularization: strip trailing 's'."""
    if name.endswith("s") and len(name) > 1:
        return name[:-1]
    return name


def _list_item_type(
    annotation: object,
) -> type | None:
    """Extract ``T`` from ``list[T]``, or ``None``."""
    origin = getattr(annotation, "__origin__", None)
    if origin is not list:
        return None
    args: tuple[type, ...] = getattr(annotation, "__args__", ())
    if not args:
        return None
    return args[0]
