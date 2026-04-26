"""General-purpose runtime utilities used by generated apps."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def run_once(fn: Callable[..., None]) -> Callable[..., None]:
    """Idempotency decorator: run ``fn`` once, ignore later calls.

    Unlike :func:`functools.cache`, the gate is *argument-blind* --
    a second call with a different argument set is still a no-op,
    not a fresh execution keyed on the new args.  This is the
    correct shape for one-shot setup functions: calling
    ``init_telemetry(app1)`` then ``init_telemetry(app2)`` must not
    install a second tracer provider or instrument a second
    FastAPI app.

    The wrapped function's return value is discarded so callers
    can't accidentally rely on a "first call's return" pattern,
    which would leak the gate to the public API surface.
    """
    sentinel = object()
    state: list[object] = [sentinel]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        if state[0] is not sentinel:
            return
        state[0] = None
        fn(*args, **kwargs)

    return wrapper
