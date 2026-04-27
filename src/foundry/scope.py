"""Scope discovery from Pydantic config models.

A scope represents a level in the config tree at which
operations can run.  Scopes are declared *explicitly* on the
config schema with the :class:`Scoped` ``Annotated``-metadata
marker::

   class ProjectConfig(FoundryConfig):
       apps: Annotated[list[App], Scoped(name="app")] = Field(
           default_factory=list,
       )

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
    from collections.abc import Iterator

    from pydantic.fields import FieldInfo


@dataclass(frozen=True)
class Scoped:
    """``Annotated`` marker tagging a list field as a foundry scope.

    Attach to a ``list[SomeBaseModel]`` field on a
    :class:`~foundry.config.FoundryConfig` subclass to declare
    that the field defines a scope level.  Only marked fields
    produce scopes; unmarked lists are plain data.

    Attributes:
        name: The scope's name, e.g. ``"app"`` or ``"resource"``.
            Required — field names on configs are conventionally
            plural (``apps``, ``resources``), but a scope refers
            to one instance, so the name is spelled out
            explicitly rather than derived by a heuristic.

    """

    name: str


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

    def __post_init__(self) -> None:
        """Enforce that only the root ``"project"`` scope is parentless."""

        if self.parent is None and self.name != "project":
            msg = (
                f"Scope {self.name!r} has no parent; only the root "
                f"'project' scope may be parentless"
            )
            raise ValueError(msg)


# The root scope — always present.
PROJECT = Scope(name="project", config_key="")


class ScopeTree(tuple[Scope, ...]):
    """Flat collection of scopes with convenience lookups.

    Subclassing :class:`tuple` gives callers the usual
    iteration/index/`len` ergonomics for free, while the two
    methods below handle the recurring "children of X" and
    "scope for id Y" patterns so the engine, store, and
    assembler don't each reinvent the search.

    Construct from :func:`discover_scopes` output::

        tree = ScopeTree(discover_scopes(MyConfig))

    """

    __slots__ = ()

    def children_of(self, parent: Scope) -> list[Scope]:
        """Return direct children of *parent*, in discovery order."""
        return [scope for scope in self if scope.parent is parent]

    def scope_for(self, instance_id: str) -> Scope:
        """Return the :class:`Scope` an ``instance_id`` belongs to.

        Instance ids produced by the engine are dot-joined paths
        of the form ``"project.<config_key>.<index>..."``.  Each
        ``config_key`` maps to exactly one child scope at the
        current level, so the scope is recovered by walking the
        tree from :data:`PROJECT` using segment pairs.

        Args:
            instance_id: A dot-path instance id (e.g.
                ``"project.apps.0.resources.2"``).

        Returns:
            The :class:`Scope` the id terminates at.

        Raises:
            ValueError: If the id doesn't start with ``"project"``
                or references a ``config_key`` not present in
                this tree.

        """
        segments = instance_id.split(".")

        if segments[0] != "project":
            msg = f"Instance id {instance_id!r} must start with 'project'"
            raise ValueError(msg)

        current = PROJECT

        for i in range(1, len(segments), 2):
            config_key = segments[i]

            try:
                current = next(
                    scope
                    for scope in self
                    if scope.parent is current
                    and scope.config_key == config_key
                )
            except StopIteration as exc:
                msg = (
                    f"Instance id {instance_id!r} references config_key "
                    f"{config_key!r}, which is not a child of "
                    f"{current.name!r}"
                )
                raise ValueError(msg) from exc

        return current


def discover_scopes(
    config_cls: type[BaseModel],
) -> ScopeTree:
    """Derive scopes from a Pydantic model's :class:`Scoped` markers.

    The top-level config is always the ``"project"`` scope.  Each
    field declared ``Annotated[list[T], Scoped()]`` becomes a
    child scope of the current level; the item type ``T`` is then
    itself descended into to discover grandchild scopes.

    Non-list ``BaseModel`` fields (e.g. ``App.config``) are
    traversed transparently: their nested :class:`Scoped` fields
    become scopes rooted at the enclosing level, with
    ``resolve_path`` reflecting the full attribute walk.

    Each scoped item type is descended into at most once, so
    discovery always terminates.  If the same type appears in
    multiple scoped lists only the first occurrence is
    descended — subsequent ones still produce their own scope
    but no grandchildren.

    Args:
        config_cls: The Pydantic model class to inspect.

    Returns:
        :class:`ScopeTree` containing every discovered scope,
        project first.

    """
    return ScopeTree((PROJECT, *_discover(config_cls, PROJECT, (), set())))


def _discover(
    cls: type[BaseModel],
    parent: Scope,
    prefix: tuple[str, ...],
    seen: set[type[BaseModel]],
) -> Iterator[Scope]:
    """Walk *cls*'s fields and yield discovered scopes.

    Two descent modes live here:

    - ``Scoped`` list fields yield a scope and recurse into the
      item type with a fresh ``prefix`` and the new scope as
      ``parent`` (gated by *seen* to break cycles).
    - Wrapper (non-list ``BaseModel``) fields recurse transparently
      with an extended ``prefix`` and the same ``parent``, so
      scoped lists nested inside wrappers surface with a compound
      ``resolve_path`` but without creating extra scope levels.
    """

    for name, info in cls.model_fields.items():
        marker = next(
            (
                potential_marker
                for potential_marker in info.metadata
                if isinstance(potential_marker, Scoped)
            ),
            None,
        )

        if marker:
            # Scope boundary: emit the scope, then descend into the
            # list's item type with a fresh prefix and the new scope
            # as parent.  Gated by ``seen`` so recursive types
            # terminate.
            item_cls = _extract_base_model_from_scoped(cls, name, info)
            child = Scope(
                name=marker.name,
                config_key=name,
                parent=parent,
                resolve_path=(*prefix, name),
            )

            yield child

            if item_cls not in seen:
                seen |= {item_cls}
                yield from _discover(item_cls, child, (), seen)

        else:
            # Organizational wrapper (non-list BaseModel): no scope
            # here, but walk in with an extended prefix so scoped
            # lists nested inside surface with the full attribute
            # path in ``resolve_path``.
            ann = info.annotation

            if isinstance(ann, type) and issubclass(ann, BaseModel):
                yield from _discover(ann, parent, (*prefix, name), seen)


def _extract_base_model_from_scoped(
    cls: type[BaseModel],
    name: str,
    info: FieldInfo,
) -> type[BaseModel]:
    """Return the item type ``T`` from a :class:`Scoped`-marked ``list[T]``.

    Raises :class:`TypeError` if the annotation is not
    ``list[BaseModel]`` — the marker only makes sense there.
    """
    annotation = info.annotation

    if getattr(annotation, "__origin__", None) is list:
        args: tuple[type, ...] = getattr(annotation, "__args__", ())
        item = args[0] if args else None

        if isinstance(item, type) and issubclass(item, BaseModel):
            return item

    msg = (
        f"{cls.__name__}.{name} is annotated Scoped() but its "
        f"type is not list[BaseModel]: {annotation!r}"
    )

    raise TypeError(msg)
