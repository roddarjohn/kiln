"""Scope discovery from Pydantic config models.

A scope represents a level in the config tree at which
operations can run.  Scopes are derived from config fields
that hold ``list[BaseModel]`` values — each item in the list
becomes one scope instance the engine iterates over.

The top-level config is always the ``"project"`` scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class Scope:
    """A named level in the config tree.

    Attributes:
        name: Human-readable scope name, e.g. ``"resource"``.
        config_key: The config field name that holds entries
            for this scope, e.g. ``"resources"``.  Empty string
            for the root (project) scope.
        parent: The parent scope, or ``None`` for the root.

    """

    name: str
    config_key: str
    parent: Scope | None = None


# The root scope — always present.
PROJECT = Scope(name="project", config_key="")


def discover_scopes(
    config_cls: type[BaseModel],
) -> list[Scope]:
    """Derive scopes from a Pydantic model's fields.

    Inspects *config_cls* for fields whose annotation is
    ``list[SomeBaseModel]``.  Each such field becomes a scope
    whose name is the singular form of the field name (trailing
    ``"s"`` stripped) and whose ``config_key`` is the field name.

    The project scope is always first in the returned list.

    Args:
        config_cls: The Pydantic model class to inspect.

    Returns:
        Ordered list of scopes: project first, then
        discovered child scopes in field-definition order.

    """
    scopes: list[Scope] = [PROJECT]
    seen: set[str] = set()

    for name, info in config_cls.model_fields.items():
        inner = _list_item_type(info.annotation)
        if inner is None:
            continue
        if not (isinstance(inner, type) and issubclass(inner, BaseModel)):
            continue
        scope_name = _singularize(name)
        if scope_name in seen:
            continue
        seen.add(scope_name)
        scopes.append(
            Scope(
                name=scope_name,
                config_key=name,
                parent=PROJECT,
            )
        )

    return scopes


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
