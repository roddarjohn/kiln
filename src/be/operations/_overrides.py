"""Shared helper for resolving per-op overrides.

After-children ops (auth, rate-limit, ...) walk every operation on
a resource and resolve an effective per-op value through a small
inheritance chain: op-level override → resource-level fallback,
optionally with a sentinel (e.g. ``False``) that means "explicitly
disabled".  This module captures that pattern in one place so each
op stays a one-liner.

Example -- auth's per-op ``require_auth`` resolution (no disable
sentinel; ``False`` is a legitimate value meaning 'allow anonymous')::

    op_auth = resolve_op_overrides(
        resource.operations,
        attr="require_auth",
        inherited=resource.require_auth,
    )

Example -- rate-limit's per-op resolution with a ``False`` kill::

    op_limits = resolve_op_overrides(
        resource.operations,
        attr="rate_limit",
        inherited=resource_limit,
        disable=False,
    )
"""

from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import OperationConfig

_UNSET = object()
"""Sentinel marking an unprovided ``disable`` argument so callers
who want ``False`` to be a *legitimate* attribute value (e.g. auth's
``require_auth=False`` meaning 'allow anonymous') aren't forced to
pretend it's a kill switch."""


@overload
def resolve_op_overrides[T](
    operations: Iterable[OperationConfig],
    *,
    attr: str,
    inherited: T,
) -> dict[str, T]: ...


@overload
def resolve_op_overrides[T](
    operations: Iterable[OperationConfig],
    *,
    attr: str,
    inherited: T,
    disable: object,
) -> dict[str, T | None]: ...


def resolve_op_overrides[T](  # type: ignore[misc]
    operations: Iterable[OperationConfig],
    *,
    attr: str,
    inherited: T,
    disable: object = _UNSET,
) -> dict[str, T | None]:
    """Map each op's name to its resolved per-op value.

    For each :class:`~be.config.schema.OperationConfig` in
    *operations*, the value at *attr* is interpreted as:

    * Equal to *disable* (when provided) → ``None`` -- the op
      explicitly opts out.
    * ``None`` → *inherited* -- the op inherits the fallback.
    * Anything else → returned as-is.

    When *disable* is omitted, the function never produces ``None``
    via the disable path -- the only ``None`` in the output comes
    from an *inherited* that itself is ``None``.  The two
    ``@overload`` signatures encode this so callers without a
    ``disable`` get back a ``dict[str, T]`` rather than
    ``dict[str, T | None]``.

    Args:
        operations: The resource's operation configs.
        attr: Attribute name on each op carrying the override.
        inherited: Value used when the op leaves the attribute unset.
        disable: Sentinel that maps to ``None`` in the output.  Omit
            to treat every non-``None`` value (including ``False``)
            as a real value.

    Returns:
        ``{op_name: effective_value}``.  ``None`` entries indicate
        the op opted out via the disable sentinel.

    """
    out: dict[str, T | None] = {}

    for op in operations:
        value = getattr(op, attr)

        if disable is not _UNSET and value == disable:
            out[op.name] = None

        elif value is None:
            out[op.name] = inherited

        else:
            out[op.name] = value

    return out
