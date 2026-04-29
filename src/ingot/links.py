"""Built-in link schemas for cross-resource references.

A *link* is the structured payload returned by ``_values``
endpoints (resource-level search, ref filter autocomplete) and
by the saved-view dump path when a stored ref is hydrated.  The
FE switches on the ``type`` discriminator and renders the
remaining fields per the schema.

The set is deliberately small.  ``LinkIDName`` covers nearly
every resource; ``LinkName`` and ``LinkID`` exist for the rare
label-only / id-only cases.  Resources that need richer rendering
(subtitles, statuses) extend this set in a single place rather
than each resource defining its own shape — uniformity is the
point.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class LinkName(BaseModel):
    """Label-only link.

    Used for resources whose display is purely a name with no
    primary-key semantics (rare).
    """

    type: str
    name: str


class LinkID(BaseModel):
    """ID-only link.

    Used when the caller only needs the primary key (rare; saved
    views and most ref filters want a label too).
    """

    type: str
    id: Any


class LinkIDName(BaseModel):
    """ID + name link.

    The default for most resources.  ``id`` is the primary key
    (typed ``Any`` because it varies per resource — uuid, int,
    str); ``name`` is the human-readable display.
    """

    type: str
    id: Any
    name: str
