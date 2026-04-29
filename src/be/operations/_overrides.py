"""Inheritance cascade for per-op configuration.

After-children ops (auth, rate-limit, tracing, ...) all resolve a
per-op effective value through the same shape: walk an
inheritance chain (op-level → resource-level → project-level → ...)
and pick the first explicitly-set value.  Some chains also have a
disable sentinel (``False`` for rate-limit / tracing) that
short-circuits the walk -- "explicitly off, stop looking".

Two helpers:

* :func:`cascade` -- the cascade primitive.  Takes any sequence of
  level values and returns the resolved one (or ``None`` when no
  level set a value, or when the disable sentinel was encountered).

* :func:`resolve_op_overrides` -- applies :func:`cascade` to every
  op on a resource and returns ``{op_name: effective_value}``.

Example -- auth's per-op ``require_auth`` (no disable; ``False``
is a legitimate "anonymous" value)::

    op_auth = resolve_op_overrides(
        resource.operations,
        attr="require_auth",
        fallbacks=(resource.require_auth,),
    )

Example -- rate-limit's per-op resolution with a ``False`` kill
and a two-level fallback::

    op_limits = resolve_op_overrides(
        resource.operations,
        attr="rate_limit",
        fallbacks=(resource.rate_limit, project.rate_limit.default_limit),
        disable=False,
    )

Example -- tracing's per-op cascade where the project-level
fallback varies by op type::

    project_toggle = (
        telemetry.span_per_action
        if op.type == "action"
        else telemetry.span_per_handler
    )
    traced = cascade(
        op.trace, resource.trace, project_toggle, disable=False
    )
"""

from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import OperationConfig

_UNSET = object()
"""Sentinel marking an unprovided ``disable`` argument so callers
who want ``False`` to be a *legitimate* value (e.g. auth's
``require_auth=False`` meaning 'allow anonymous') aren't forced to
pretend it's a kill switch."""


def cascade[T](*levels: T | None, disable: object = _UNSET) -> T | None:
    """Return the first explicitly-set value from *levels*.

    Walks *levels* in order and returns the first value that is
    not ``None``.  When *disable* is provided and any level equals
    it, the cascade short-circuits to ``None`` -- that level
    explicitly opted out, so no later fallback should apply.

    Args:
        *levels: Inheritance chain, most-specific first.  Each
            level is the value at that scope (op → resource →
            project → ...).  ``None`` means "this level didn't
            set a value, defer to the next".
        disable: Sentinel value (typically ``False``) that, when
            encountered at any level, returns ``None`` immediately.
            Omit when no level has a kill semantic.

    Returns:
        The first non-``None`` value in *levels*, or ``None`` when
        every level is ``None`` or the disable sentinel was hit.

    """
    for value in levels:
        if disable is not _UNSET and value == disable:
            return None

        if value is not None:
            return value

    return None


@overload
def resolve_op_overrides[T](
    operations: Iterable[OperationConfig],
    *,
    attr: str,
    fallbacks: tuple[T | None, ...],
) -> dict[str, T | None]: ...


@overload
def resolve_op_overrides[T](
    operations: Iterable[OperationConfig],
    *,
    attr: str,
    fallbacks: tuple[T | None, ...],
    disable: object,
) -> dict[str, T | None]: ...


def resolve_op_overrides[T](
    operations: Iterable[OperationConfig],
    *,
    attr: str,
    fallbacks: tuple[T | None, ...],
    disable: object = _UNSET,
) -> dict[str, T | None]:
    """Map each op's name to its cascaded per-op value.

    Composes :func:`cascade` over each op: the op's own value at
    *attr* sits at the head of the chain, followed by *fallbacks*
    in order.  The first non-``None`` level wins; *disable* at any
    level short-circuits to ``None``.

    Args:
        operations: The resource's operation configs.
        attr: Attribute name on each op carrying the override.
        fallbacks: Inheritance chain *below* the op level, in
            most-specific-first order (typically
            ``(resource_value, project_value)``).
        disable: Sentinel that maps to ``None`` in the output.
            Omit to treat every value (including ``False``) as a
            real value.

    Returns:
        ``{op_name: effective_value}``.  ``None`` entries indicate
        the cascade resolved to no value -- either the disable
        sentinel was hit or every level was ``None``.

    """
    return {
        op.name: cascade(getattr(op, attr), *fallbacks, disable=disable)
        for op in operations
    }
