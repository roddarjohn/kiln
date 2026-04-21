"""Render registry for output types.

The ``@renders`` decorator registers a function that knows how
to turn a build output into a code string.  The engine calls
renderers after all operations have run their build phase.

A renderer is a callable ``(output, RenderCtx) -> str``.
Multiple renderers can be registered for the same output type;
the *when* predicate selects the first match at render time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import jinja2


@dataclass(frozen=True)
class RenderCtx:
    """Context passed to every renderer function.

    Attributes:
        env: Jinja2 environment for template lookups.
        config: The full project config dict (or model).
        package_prefix: Dotted prefix for generated imports,
            e.g. ``"_generated"``.

    """

    env: jinja2.Environment
    config: Any
    package_prefix: str = ""


# Type aliases for readability.
_Predicate = Callable[[Any], bool]
_RendererFn = Callable[[Any, RenderCtx], str]


@dataclass
class _RendererEntry:
    """One registered renderer with its optional guard."""

    fn: _RendererFn
    when: _Predicate | None = None


class RenderRegistry:
    """Maps output types to renderer functions.

    Renderers are tried in registration order.  The first
    whose *when* predicate returns ``True`` (or that has no
    predicate) wins.

    Example::

        registry = RenderRegistry()

        @registry.renders(RouteHandler)
        def render_route(handler, ctx):
            return ctx.env.get_template("handler.j2").render(
                h=handler,
            )

    """

    def __init__(self) -> None:  # noqa: D107
        self._entries: dict[type, list[_RendererEntry]] = {}

    def renders(
        self,
        output_type: type,
        *,
        when: _Predicate | None = None,
    ) -> Callable[[_RendererFn], _RendererFn]:
        """Register a renderer for *output_type*.

        Args:
            output_type: The output class this renderer handles.
            when: Optional predicate receiving the config;
                renderer is skipped when it returns ``False``.

        Returns:
            The original function, unmodified.

        """

        def decorator(fn: _RendererFn) -> _RendererFn:
            entries = self._entries.setdefault(output_type, [])
            entries.append(_RendererEntry(fn=fn, when=when))
            return fn

        return decorator

    def render(
        self,
        obj: object,
        ctx: RenderCtx,
    ) -> str:
        """Render a build output using the first matching renderer.

        Args:
            obj: The build output to render.
            ctx: Render context with env and config.

        Returns:
            Rendered code string.

        Raises:
            LookupError: No renderer registered for the type.

        """
        output_type = type(obj)
        entries = self._entries.get(output_type, [])
        for entry in entries:
            if entry.when is None or entry.when(ctx.config):
                return entry.fn(obj, ctx)
        msg = f"No renderer for {output_type.__name__}"
        raise LookupError(msg)

    def has_renderer(self, output_type: type) -> bool:
        """Return whether any renderer is registered for *output_type*."""
        return output_type in self._entries


@dataclass
class BuildStore:
    """Accumulator for objects produced during the build phase.

    Objects are keyed by ``(scope_name, instance_id, op_name)``
    so the engine and later operations can query earlier output.

    Attributes:
        _items: Internal storage mapping keys to object lists.

    """

    _items: dict[tuple[str, str, str], list[object]] = field(
        default_factory=dict
    )

    def add(
        self,
        scope: str,
        instance_id: str,
        op_name: str,
        *objects: object,
    ) -> None:
        """Store build outputs for a build step.

        Args:
            scope: Scope name (e.g. ``"resource"``).
            instance_id: Instance identifier within the scope.
            op_name: Operation name that produced these objects.
            *objects: The build outputs to store.

        """
        key = (scope, instance_id, op_name)
        self._items.setdefault(key, []).extend(objects)

    def get(
        self,
        scope: str,
        instance_id: str,
        op_name: str,
    ) -> list[object]:
        """Retrieve build outputs for a specific build step.

        Args:
            scope: Scope name.
            instance_id: Instance identifier.
            op_name: Operation name.

        Returns:
            List of build outputs, empty if none stored.

        """
        return list(self._items.get((scope, instance_id, op_name), []))

    def get_by_scope(
        self,
        scope: str,
        instance_id: str,
    ) -> list[object]:
        """Retrieve all build outputs for a scope instance.

        Args:
            scope: Scope name.
            instance_id: Instance identifier.

        Returns:
            All build outputs across all operations for this
            scope instance.

        """
        result: list[object] = []
        for (s, iid, _), items in self._items.items():
            if s == scope and iid == instance_id:
                result.extend(items)
        return result

    def get_by_type(self, output_type: type) -> list[object]:
        """Retrieve all build outputs of a given type.

        Args:
            output_type: The output class to filter by.

        Returns:
            All matching build outputs across all keys.

        """
        result: list[object] = []
        for items in self._items.values():
            result.extend(obj for obj in items if isinstance(obj, output_type))
        return result

    def all_items(self) -> list[object]:
        """Return every stored build output."""
        result: list[object] = []
        for items in self._items.values():
            result.extend(items)
        return result
