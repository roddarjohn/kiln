"""General-purpose runtime utilities used by generated apps.

Two unrelated concerns share this module by convention --
:func:`get_object_from_query_or_404` used by every read-or-mutate
CRUD handler, and the :func:`run_once` decorator used by the
generated telemetry init.  Bundling them here keeps the public
``ingot`` surface flat enough that consumers learn one import path
(``from ingot.utils import ...``) for everything that doesn't fit
under a more specific submodule.
"""

import functools
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession


# -------------------------------------------------------------------
# HTTP-status row-lookup guards
# -------------------------------------------------------------------


async def get_object_from_query_or_404(
    db: AsyncSession,
    stmt: Any,
    *,
    detail: str = "Not found",
) -> Any:
    """Execute *stmt* and return the first row, or raise HTTP 404.

    Args:
        db: The async database session.
        stmt: A SQLAlchemy selectable statement.
        detail: The error message for the 404 response.

    Returns:
        The first row from the result set.

    Raises:
        HTTPException: With status 404 when no row is found.

    """
    result = await db.execute(stmt)
    row = result.scalars().one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )

    return row


# -------------------------------------------------------------------
# Once-only execution
# -------------------------------------------------------------------


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
    called = False

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        nonlocal called

        if called:
            return

        called = True
        fn(*args, **kwargs)

    return wrapper
