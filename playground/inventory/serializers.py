"""Custom serializers wired into kiln resources.

Currently:

* :func:`dump_view_hydrated` — used by the ``SavedView`` resource's
  ``get`` and ``list`` ops.  Walks the view's stored payload
  through :func:`ingot.saved_views.hydrate_view`, which resolves
  ``ref`` / ``self`` filter ids to link-schema items via the
  per-app ``REF_RESOLVERS`` registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ingot.saved_views import hydrate_view

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from inventory.models import SavedView
    from myapp.auth import Session


async def dump_view_hydrated(
    view: SavedView, session: Session, db: AsyncSession
) -> dict[str, Any]:
    """Hydrate ref / self filter ids on read so the FE sees labels.

    Imports ``REF_RESOLVERS`` lazily because the module is
    code-generated under ``_generated/`` and only exists after
    ``foundry generate`` has run.  Lazy import keeps mypy happy
    on a fresh checkout.
    """
    from _generated.inventory.links import REF_RESOLVERS

    return await hydrate_view(view, REF_RESOLVERS, db, session)
