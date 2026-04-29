"""Per-op cascade resolver for be operation configs.

After-children ops (auth, rate-limit, tracing, ...) all resolve a
per-op effective value through the same shape: walk an
inheritance chain (op-level → resource-level → project-level → ...)
and pick the first explicitly-set value.  The pure cascade
primitive lives in :mod:`foundry.cascade` so any plugin can use
it; this module binds it to be's
:class:`~be.config.schema.OperationConfig` to produce
``{op_name: effective_value}`` dicts in one call.

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

When the project-level fallback varies per op (e.g. tracing's
``span_per_action`` vs ``span_per_handler``), call
:func:`~foundry.cascade.cascade` directly per op rather than
going through this bulk helper.
"""

from typing import TYPE_CHECKING, overload

from foundry.cascade import _UNSET, cascade

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import OperationConfig


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


def resolve_op_overrides[T](  # type: ignore[misc]
    operations: Iterable[OperationConfig],
    *,
    attr: str,
    fallbacks: tuple[T | None, ...],
    disable: object = _UNSET,
) -> dict[str, T | None]:
    """Map each op's name to its cascaded per-op value.

    Composes :func:`~foundry.cascade.cascade` over each op: the
    op's own value at *attr* sits at the head of the chain,
    followed by *fallbacks* in order.  The first non-``None``
    level wins; *disable* at any level short-circuits to ``None``.

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
