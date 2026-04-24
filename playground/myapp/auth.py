"""Consumer-owned auth types consumed by the kiln-generated package.

Three dotted paths referenced from ``examples/project.jsonnet``:

* :class:`LoginCredentials` -- request body of ``POST /auth/token``.
* :class:`Session` -- Pydantic model the JWT carries.
* :func:`validate_login` -- ``(creds) -> Session | None`` called by
  the generated login route.

The :attr:`Session.jti` field is not required by the generator; it's
added here so the server-side revocation store in
:mod:`myapp.revocation` has a stable handle per token.  Without a
revocation layer the field is simply an unused claim.

Stub credentials check: ``alice`` / ``wonderland`` succeeds;
anything else returns ``None`` (generated route converts that to a
401).  Swap in a real user-table lookup in a real project.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class LoginCredentials(BaseModel):
    """JSON body accepted by the login endpoint."""

    username: str
    password: str


class Session(BaseModel):
    """Claims carried in the JWT and handed to protected routes."""

    sub: str
    jti: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """JWT ID.  Consumer-minted so :mod:`myapp.revocation` has a
    handle to key the deny-list on.  Generator ignores it."""


def validate_login(creds: LoginCredentials) -> Session | None:
    """Return a :class:`Session` on success, ``None`` to reject."""

    if creds.username == "alice" and creds.password == "wonderland":  # noqa: S105
        return Session(sub=creds.username)

    return None
