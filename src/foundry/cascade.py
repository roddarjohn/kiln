"""Inheritance cascade primitive for layered configuration.

Code-generation operations frequently resolve a value through a
small inheritance chain -- per-op override → resource-level
default → project-level default -- where the most-specific
explicit value wins, ``None`` defers to the next level, and an
optional sentinel (typically ``False``) short-circuits the walk
to "explicitly off".  :func:`cascade` is the pure function that
implements that walk.

It lives in foundry rather than a target plugin so any plugin
(``be``, ``fe``, future ones) can compose it the same way.
Plugin-specific helpers that bind a cascade to a config tree
(e.g. :func:`be.operations._overrides.resolve_op_overrides`,
which runs a cascade per
:class:`~be.config.schema.OperationConfig`) build on top of this
primitive.

Example::

    from foundry.cascade import cascade

    # Project default → resource override → op override.
    effective = cascade(
        op.rate_limit,
        resource.rate_limit,
        project.rate_limit.default_limit,
        disable=False,
    )
"""

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
