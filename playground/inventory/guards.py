"""Authorisation guards for the 'inventory' module.

Each guard is an ``async (resource, session) -> bool`` callable
that the generated route handler awaits.  Returning ``False``
flips the response to 403 Forbidden; returning ``True`` lets the
op proceed.  Used by the action-framework surface
(:attr:`be.config.schema.OperationConfig.can`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory.models import SavedView
    from myapp.auth import Session


async def is_view_owner(view: SavedView, session: Session) -> bool:
    """True iff *session* owns *view*.

    Saved views scope per-user via ``owner_id``; the generated
    ``list`` and ``get`` routes use this guard to filter rows the
    caller didn't create.  ``session.sub`` is the JWT subject
    claim — whatever your auth flow stamps as the user identity.
    """
    return view.owner_id == str(session.sub)
