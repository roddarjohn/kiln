"""Consumer-owned session store for DB-backed revocation.

Implements :class:`ingot.auth.SessionStore` as an in-memory
deny-list (stand-in for a real ``revoked_sessions`` table).  The
generator wires ``revocations`` into both the ``get_session``
dependency (so protected routes reject revoked tokens) and the
logout handler (so ``POST /auth/token/logout`` calls
:meth:`revoke` before clearing the cookie) via the
``session_store`` field in the project jsonnet.

Swap the in-memory ``set`` for a SQLAlchemy table with
``jti PK, revoked_at, expires_at`` plus a nightly cleanup for the
production version -- the protocol stays the same.
"""

from __future__ import annotations

from myapp.auth import Session


class RevocationStore:
    """In-memory deny-list keyed on ``session.jti``.

    Implements :class:`ingot.auth.SessionStore`.  The methods are
    async so a DB-backed implementation drops in without touching
    callers; the in-memory impl just returns immediately.
    """

    def __init__(self) -> None:
        self._revoked: set[str] = set()

    async def is_revoked(self, session: Session) -> bool:
        """Return whether *session* has been revoked."""
        return session.jti in self._revoked

    async def revoke(self, session: Session) -> None:
        """Mark *session* as revoked.  Idempotent."""
        self._revoked.add(session.jti)


revocations = RevocationStore()
