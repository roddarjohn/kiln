"""Consumer-owned auth types wired into the generator via project.jsonnet.

The ``jti`` on :class:`Session` isn't required by the generator --
it's the stable handle :mod:`myapp.revocation` keys the deny-list on.
Without a revocation layer it's an unused claim.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class LoginCredentials(BaseModel):
    """Login request body."""

    username: str
    password: str


class Session(BaseModel):
    """JWT claims."""

    sub: str
    jti: str = Field(default_factory=lambda: uuid.uuid4().hex)


def validate_login(creds: LoginCredentials) -> Session | None:
    """Return a :class:`Session` on success, ``None`` to reject."""

    if creds.username == "alice" and creds.password == "wonderland":  # noqa: S105
        return Session(sub=creds.username)

    return None
