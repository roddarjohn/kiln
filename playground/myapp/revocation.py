"""In-memory session store wired into the generator via project.jsonnet.

Stand-in for a real ``revoked_sessions(jti PK, revoked_at,
expires_at)`` table plus a cleanup job -- the protocol stays the
same when you swap the set for SQL.
"""

from __future__ import annotations

from myapp.auth import Session


class RevocationStore:
    """:class:`ingot.auth.SessionStore` keyed on ``session.jti``."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()

    async def is_revoked(self, session: Session) -> bool:
        """Return whether *session* has been revoked."""
        return session.jti in self._revoked

    async def revoke(self, session: Session) -> None:
        """Mark *session* as revoked.  Idempotent."""
        self._revoked.add(session.jti)


revocations = RevocationStore()
