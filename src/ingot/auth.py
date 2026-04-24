"""JWT auth primitives for kiln-generated FastAPI projects.

A session is a Pydantic model dumped into JWT claims.  Tokens
travel over one or both of two *sources*:

* ``"bearer"`` -- ``Authorization`` header; API clients.
* ``"cookie"`` -- ``httpOnly`` cookie; browser frontends (out of
  reach of JS so XSS can't steal it).

The signing secret lives in an env var (caller-named, typically
``JWT_SECRET``) so generated source never embeds a key.
"""

import datetime
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, Literal, Protocol

import jwt
from fastapi import Cookie, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

DEFAULT_TOKEN_TTL = datetime.timedelta(minutes=30)
"""Default ``exp`` stamped on tokens when the caller doesn't set one."""

Source = Literal["bearer", "cookie"]
SameSite = Literal["lax", "strict", "none"]


class SessionStore(Protocol):
    """Hook pair for server-side session state (deny-list, sessions, ...).

    Turns the stateless-JWT flow stateful.  The store receives the
    full session model so it can key on whatever identity claim
    the consumer picks (typically ``jti``); ``ingot.auth`` stays
    agnostic.

    Both methods are async so the store can hit a database.
    """

    async def is_revoked(self, session: BaseModel) -> bool:
        """Return ``True`` to reject the request with HTTP 401."""
        ...

    async def revoke(self, session: BaseModel) -> None:
        """Mark *session* dead.  Must be idempotent."""
        ...


def _unauthorized() -> HTTPException:
    """401 for missing or invalid tokens."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _revoked() -> HTTPException:
    """401 for JWT-valid tokens the :class:`SessionStore` rejected."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session revoked",
        headers={"WWW-Authenticate": "Bearer"},
    )


_ALLOWED_SOURCES: tuple[Source, ...] = ("bearer", "cookie")


def _require_cookie_name(
    sources: Sequence[Source], cookie_name: str | None
) -> None:
    if "cookie" in sources and cookie_name is None:
        msg = "cookie_name is required when 'cookie' is in sources"
        raise ValueError(msg)


def _validate_session_args(
    sources: Sequence[Source],
    token_url: str | None,
    cookie_name: str | None,
) -> None:
    """Runtime checks for calls that bypass the :data:`Source` Literal."""
    unknown = [s for s in sources if s not in _ALLOWED_SOURCES]
    if unknown:
        msg = f"unknown source(s): {sorted(set(unknown))}"
        raise ValueError(msg)
    if not sources:
        msg = "sources must contain at least one of 'bearer' or 'cookie'"
        raise ValueError(msg)
    if "bearer" in sources and token_url is None:
        msg = "token_url is required when 'bearer' is in sources"
        raise ValueError(msg)
    _require_cookie_name(sources, cookie_name)


def encode_jwt(
    payload: dict[str, Any],
    *,
    secret_env: str,
    algorithm: str,
    ttl: datetime.timedelta = DEFAULT_TOKEN_TTL,
) -> str:
    """Sign *payload* as a JWT; stamps ``exp`` if absent.  Never mutates."""
    claims = dict(payload)
    claims.setdefault(
        "exp",
        datetime.datetime.now(tz=datetime.UTC) + ttl,
    )
    return jwt.encode(claims, os.environ[secret_env], algorithm=algorithm)


def decode_jwt(
    token: str,
    *,
    secret_env: str,
    algorithm: str,
) -> dict[str, Any]:
    """Decode *token* and return its claims, or raise HTTP 401.

    A missing ``secret_env`` collapses to 401 (not 500) so the
    ``WWW-Authenticate`` handshake stays correct from the caller's
    perspective -- they just see "not authenticated."
    """
    try:
        return jwt.decode(token, os.environ[secret_env], algorithms=[algorithm])
    except (jwt.InvalidTokenError, KeyError) as exc:
        raise _unauthorized() from exc


def session_auth[T: BaseModel](
    schema: type[T],
    sources: Sequence[Source],
    *,
    secret_env: str,
    algorithm: str,
    token_url: str | None = None,
    cookie_name: str | None = None,
    store: SessionStore | None = None,
) -> Callable[..., Awaitable[T]]:
    """Build a FastAPI dep that yields a validated *schema* instance.

    The returned callable takes one parameter per configured
    source; the first token that's present wins.  Claims parse
    through :meth:`~pydantic.BaseModel.model_validate` so handlers
    get the full model, not a raw dict.

    *token_url* is required with ``"bearer"`` -- surfaced to
    OpenAPI via :class:`OAuth2PasswordBearer` for Swagger's
    Authorize button; runtime uses only the ``Authorization``
    header.  *cookie_name* is required with ``"cookie"``.

    *store*, when supplied, turns every authenticated request into
    a deny-list check -- avoids a wrapper dep on the consumer side.
    """
    _validate_session_args(sources, token_url, cookie_name)
    use_bearer = "bearer" in sources
    use_cookie = "cookie" in sources

    async def resolve(token: str | None) -> T:
        """Decode, validate, deny-list check — or raise 401."""
        if token is None:
            raise _unauthorized()
        claims = decode_jwt(token, secret_env=secret_env, algorithm=algorithm)
        session = schema.model_validate(claims)
        if store is not None and await store.is_revoked(session):
            raise _revoked()
        return session

    if use_bearer and use_cookie:
        assert token_url is not None  # noqa: S101 -- validated above
        assert cookie_name is not None  # noqa: S101
        oauth = OAuth2PasswordBearer(tokenUrl=token_url, auto_error=False)

        async def get_session(
            bearer: Annotated[str | None, Depends(oauth)] = None,
            cookie: Annotated[str | None, Cookie(alias=cookie_name)] = None,
        ) -> T:
            return await resolve(bearer or cookie)

    elif use_bearer:
        assert token_url is not None  # noqa: S101
        oauth = OAuth2PasswordBearer(tokenUrl=token_url, auto_error=False)

        async def get_session(  # type: ignore[misc]
            bearer: Annotated[str | None, Depends(oauth)] = None,
        ) -> T:
            return await resolve(bearer)

    else:
        assert cookie_name is not None  # noqa: S101

        async def get_session(  # type: ignore[misc]
            cookie: Annotated[str | None, Cookie(alias=cookie_name)] = None,
        ) -> T:
            return await resolve(cookie)

    return get_session


def issue_session(
    response: Response,
    session: BaseModel | None,
    *,
    sources: Sequence[Source],
    secret_env: str,
    algorithm: str,
    ttl: datetime.timedelta = DEFAULT_TOKEN_TTL,
    cookie_name: str | None = None,
    cookie_secure: bool = True,
    cookie_samesite: SameSite = "lax",
) -> dict[str, Any]:
    """Mint a JWT and emit it to every configured source.

    Collapses ``session is None`` (validate rejected the creds)
    to HTTP 401 so login handlers stay one-liners.  The JWT is
    encoded once and reused across sources; when both are
    configured the cookie and response body carry the same token.

    Returns ``{"access_token", "token_type"}`` when bearer is in
    *sources*, else ``{"ok": True}``.
    """
    if not sources:
        msg = "sources must be non-empty"
        raise ValueError(msg)
    _require_cookie_name(sources, cookie_name)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = encode_jwt(
        session.model_dump(mode="json"),
        secret_env=secret_env,
        algorithm=algorithm,
        ttl=ttl,
    )

    if "cookie" in sources:
        response.set_cookie(
            key=cookie_name,  # type: ignore[arg-type]
            value=token,
            max_age=int(ttl.total_seconds()),
            httponly=True,
            secure=cookie_secure,
            samesite=cookie_samesite,
        )

    if "bearer" in sources:
        return {"access_token": token, "token_type": "bearer"}
    return {"ok": True}


def clear_session(
    response: Response,
    *,
    sources: Sequence[Source],
    cookie_name: str | None = None,
    cookie_secure: bool = True,
    cookie_samesite: SameSite = "lax",
) -> dict[str, bool]:
    """Delete the session cookie if configured; ack for bearer.

    ``cookie_secure`` and ``cookie_samesite`` must match the values
    :func:`issue_session` used -- browsers refuse to overwrite an
    existing cookie when either attribute differs.
    """
    _require_cookie_name(sources, cookie_name)
    if "cookie" in sources:
        response.delete_cookie(
            key=cookie_name,  # type: ignore[arg-type]
            httponly=True,
            secure=cookie_secure,
            samesite=cookie_samesite,
        )
    return {"ok": True}
