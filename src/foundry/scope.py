"""Scope discovery from Pydantic config models.

A scope represents a level in the config tree at which
operations can run.  Scopes are declared *explicitly* on the
config schema with the :class:`Scoped` ``Annotated``-metadata
marker::

   class ProjectConfig(FoundryConfig):
       apps: Annotated[list[App], Scoped()] = Field(default_factory=list)

Each such field becomes a scope, and every item in the list is one
scope instance the engine iterates over.  Unannotated
``list[BaseModel]`` fields are treated as ordinary data, not
scopes — so target authors can carry lists of nested models
without accidentally creating new scope levels.

Scopes form a tree rooted at :data:`PROJECT`.  A child scope's
instances are resolved from its parent scope instance by walking
:attr:`Scope.resolve_path` — a tuple of attribute names.  For a
directly-annotated field this is just ``(field_name,)``; for
fields that sit inside an intermediate non-list ``BaseModel``
wrapper (e.g. ``App.config.resources``) the path is the full
attribute walk, e.g. ``("config", "resources")``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo


@dataclass(frozen=True)
class Scoped:
    """``Annotated`` marker tagging a list field as a foundry scope.

    Attach to a ``list[SomeBaseModel]`` field on a
    :class:`~foundry.config.FoundryConfig` subclass to declare
    that the field defines a scope level.  Only marked fields
    produce scopes; unmarked lists are plain data.

    Attributes:
        name: Optional scope-name override.  When ``None`` the
            scope name is derived from the field name by stripping
            a trailing ``s`` (``"apps"`` → ``"app"``).  Set
            explicitly when that heuristic is wrong.

    """

    name: str | None = None


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
    """Derive scopes from a Pydantic model's :class:`Scoped` markers.

    The top-level config is always the ``"project"`` scope.  Each
    field declared ``Annotated[list[T], Scoped()]`` becomes a
    child scope of the current level; the item type ``T`` is then
    itself descended into to discover grandchild scopes.

    Non-list ``BaseModel`` fields (e.g. ``App.config``) are
    traversed transparently: their nested :class:`Scoped` fields
    become scopes rooted at the enclosing level, with
    ``resolve_path`` reflecting the full attribute walk.

    Cycles are detected via a visited-classes set so discovery
    always terminates.

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
    """Walk *cls*'s fields, appending :class:`Scoped`-marked ones to *out*.

    The same scope *name* may appear at multiple places in the
    tree (e.g. ``resource`` may live directly under the project
    *and* inside each ``app``).  That's intentional: operations
    dispatch by scope name, so they run at every matching node.

    Cycle detection tracks only classes reached via a
    :class:`Scoped` list descent, because those are what can
    nest unboundedly.  Non-list ``BaseModel`` fields (e.g.
    ``App.config``) are traversed transparently so nested scoped
    fields surface with a compound ``resolve_path``.

    Args:
        cls: The Pydantic model class to inspect.
        parent: The scope whose instance hosts these fields.
        path_prefix: Attribute path accumulated so far from the
            parent scope instance to ``cls``.  Empty when
            ``cls`` is itself the parent scope's instance type.
        seen_lists: Item types already entered via a scoped list
            descent; used to break cycles without blocking the
            wrapper traversal above.
        out: Output list; mutated in place.

    """
    for name, info in cls.model_fields.items():
        scoped = _scope_info(cls, name, info)
        if scoped is not None:
            marker, item_cls = scoped
            child = Scope(
                name=marker.name or _singularize(name),
                config_key=name,
                parent=parent,
                resolve_path=(*path_prefix, name),
            )
            out.append(child)
            if item_cls not in seen_lists:
                _discover(
                    item_cls,
                    parent=child,
                    path_prefix=(),
                    seen_lists=seen_lists | {item_cls},
                    out=out,
                )
            continue

        ann = info.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            _discover(
                ann,
                parent=parent,
                path_prefix=(*path_prefix, name),
                seen_lists=seen_lists,
                out=out,
            )


def _scope_info(
    cls: type[BaseModel],
    name: str,
    info: FieldInfo,
) -> tuple[Scoped, type[BaseModel]] | None:
    """Return ``(marker, item_type)`` if *info* is a scoped list field.

    Returns ``None`` when the field has no :class:`Scoped` marker.
    Raises :class:`TypeError` when the marker is present but the
    field's annotation is not ``list[BaseModel]`` — the marker
    only makes sense on a list of models.
    """
    marker = next(
        (m for m in info.metadata if isinstance(m, Scoped)),
        None,
    )
    if marker is None:
        return None
    item = _list_item_type(info.annotation)
    if item is None or not (
        isinstance(item, type) and issubclass(item, BaseModel)
    ):
        msg = (
            f"{cls.__name__}.{name} is annotated Scoped() but its "
            f"type is not list[BaseModel]: {info.annotation!r}"
        )
        raise TypeError(msg)
    return marker, item


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
