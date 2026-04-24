"""JWT auth primitives for kiln-generated FastAPI projects.

Two transports are supported:

* **bearer** -- the JWT is carried in the ``Authorization: Bearer``
  header, which is the OAuth2 password-flow convention and the right
  choice for API clients that manage their own tokens.
* **cookie** -- the JWT is set as an ``httpOnly`` cookie by the login
  endpoint.  Browsers then attach it automatically, which is simpler
  for SPA / server-rendered frontends and keeps the token out of
  reach of JS (mitigating XSS token theft).

Both transports sign the same JWT with the same secret and carry the
same typed session payload; only the carrier differs.

The session is a caller-supplied Pydantic model.
:func:`bearer_auth` / :func:`cookie_auth` return a dependency that
parses validated JWT claims into an instance of that model;
:func:`issue_bearer_token` / :func:`set_auth_cookie` accept an
instance of the same model and dump it into the token.

The secret is read from an environment variable named by the caller
(typically ``JWT_SECRET``) so that the rendered source never embeds
a key.
"""

from __future__ import annotations

import datetime
import os
from typing import TYPE_CHECKING, Annotated, Any, Literal

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Response

DEFAULT_TOKEN_TTL = datetime.timedelta(minutes=30)
"""Lifetime stamped onto tokens when the caller does not set ``exp``."""

SameSite = Literal["lax", "strict", "none"]


def encode_jwt(
    payload: dict[str, Any],
    *,
    secret_env: str,
    algorithm: str,
    ttl: datetime.timedelta = DEFAULT_TOKEN_TTL,
) -> str:
    """Sign *payload* as a JWT using the secret at ``os.environ[secret_env]``.

    An ``exp`` claim is stamped ``ttl`` into the future if the caller
    has not already supplied one.  The input dict is never mutated.

    Args:
        payload: JWT claims to encode.
        secret_env: Environment variable holding the signing secret.
        algorithm: JWT signing algorithm (e.g. ``"HS256"``).
        ttl: Lifetime applied when *payload* lacks an ``exp`` claim.

    Returns:
        The encoded JWT as a string.

    Raises:
        KeyError: If ``secret_env`` is not set.

    """
    claims = dict(payload)
    claims.setdefault(
        "exp",
        datetime.datetime.now(tz=datetime.UTC) + ttl,
    )
    secret = os.environ[secret_env]
    return jwt.encode(claims, secret, algorithm=algorithm)


def decode_jwt(
    token: str,
    *,
    secret_env: str,
    algorithm: str,
) -> dict[str, Any]:
    """Decode *token* and return its claims, or raise HTTP 401.

    A missing ``secret_env`` is treated as a 401 rather than a 500
    because, from the caller's perspective, the server simply cannot
    validate the token -- collapsing both failure modes keeps the
    WWW-Authenticate handshake correct.

    Args:
        token: Encoded JWT.
        secret_env: Environment variable holding the signing secret.
        algorithm: Expected signing algorithm.

    Returns:
        The decoded payload.

    Raises:
        HTTPException: 401 if the token is invalid, expired, or the
            secret env var is unset.

    """
    try:
        secret = os.environ[secret_env]
        return jwt.decode(token, secret, algorithms=[algorithm])
    except (jwt.InvalidTokenError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def bearer_auth(
    schema: type[SessionT],
    *,
    token_url: str,
    secret_env: str,
    algorithm: str,
) -> Callable[..., Awaitable[SessionT]]:
    """Build a ``get_session`` dependency for bearer-token auth.

    The returned callable is suitable for ``Depends(...)`` and reads
    the token via :class:`fastapi.security.OAuth2PasswordBearer`, so
    Swagger's *Authorize* flow wires up automatically.  The JWT
    claims are validated into an instance of *schema* before the
    dep returns.

    Args:
        schema: Pydantic model describing the session payload.
        token_url: Path of the login endpoint, surfaced to OpenAPI.
        secret_env: Environment variable holding the signing secret.
        algorithm: Expected signing algorithm.

    Returns:
        An async dependency that yields a *schema* instance.

    """
    oauth2_scheme = OAuth2PasswordBearer(tokenUrl=token_url)

    async def get_session(
        token: Annotated[str, Depends(oauth2_scheme)],
    ) -> SessionT:
        claims = decode_jwt(
            token,
            secret_env=secret_env,
            algorithm=algorithm,
        )
        return schema.model_validate(claims)

    return get_session


def cookie_auth(
    schema: type[SessionT],
    *,
    cookie_name: str,
    secret_env: str,
    algorithm: str,
) -> Callable[..., Awaitable[SessionT]]:
    """Build a ``get_session`` dependency for cookie-based auth.

    Reads the JWT from a named cookie using :class:`fastapi.Cookie`.
    A missing cookie maps to HTTP 401 with the same
    ``WWW-Authenticate: Bearer`` header used by the header-based
    transport, so clients see a consistent failure shape regardless
    of transport.  Validated claims are parsed into *schema*.

    Args:
        schema: Pydantic model describing the session payload.
        cookie_name: Name of the cookie carrying the JWT.
        secret_env: Environment variable holding the signing secret.
        algorithm: Expected signing algorithm.

    Returns:
        An async dependency that yields a *schema* instance.

    """

    async def get_session(
        token: Annotated[str | None, Cookie(alias=cookie_name)] = None,
    ) -> SessionT:
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        claims = decode_jwt(
            token,
            secret_env=secret_env,
            algorithm=algorithm,
        )
        return schema.model_validate(claims)

    return get_session


def issue_bearer_token(
    session: BaseModel | None,
    *,
    secret_env: str,
    algorithm: str,
    ttl: datetime.timedelta = DEFAULT_TOKEN_TTL,
) -> dict[str, str]:
    """Return an OAuth2-shaped token response for a login endpoint.

    Collapses the "validate returned None" case into a 401 so login
    handlers stay one-liners.  Callers pass the result of their
    validation function directly.

    Args:
        session: Validated session model, or ``None`` when
            credentials did not match.
        secret_env: Environment variable holding the signing secret.
        algorithm: JWT signing algorithm.
        ttl: Token lifetime.

    Returns:
        ``{"access_token": <jwt>, "token_type": "bearer"}``.

    Raises:
        HTTPException: 401 if *session* is ``None``.

    """
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
    return {"access_token": token, "token_type": "bearer"}


def set_auth_cookie(
    response: Response,
    session: BaseModel | None,
    *,
    cookie_name: str,
    secret_env: str,
    algorithm: str,
    ttl: datetime.timedelta = DEFAULT_TOKEN_TTL,
    secure: bool = True,
    samesite: SameSite = "lax",
) -> None:
    """Mint a JWT and set it as an ``httpOnly`` cookie on *response*.

    ``httpOnly`` is always ``True`` -- the whole point of cookie
    transport versus ``localStorage`` is to keep the token out of
    JS.  ``secure`` and ``samesite`` are configurable because local
    development over plain HTTP would otherwise drop the cookie.

    Args:
        response: The FastAPI response that will carry the cookie.
        session: Validated session model, or ``None`` on failure.
        cookie_name: Name of the cookie.
        secret_env: Environment variable holding the signing secret.
        algorithm: JWT signing algorithm.
        ttl: Token lifetime; also used as cookie ``max_age``.
        secure: Whether the ``Secure`` flag is set.
        samesite: SameSite attribute value.

    Raises:
        HTTPException: 401 if *session* is ``None``.

    """
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
    response.set_cookie(
        key=cookie_name,
        value=token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=secure,
        samesite=samesite,
    )


def clear_auth_cookie(
    response: Response,
    *,
    cookie_name: str,
    secure: bool = True,
    samesite: SameSite = "lax",
) -> None:
    """Delete the auth cookie from the client.

    The ``secure`` and ``samesite`` values must match what
    :func:`set_auth_cookie` used, otherwise browsers refuse to
    overwrite the existing cookie.

    Args:
        response: The FastAPI response that will carry the Set-Cookie
            header that deletes the cookie.
        cookie_name: Name of the cookie to clear.
        secure: Must match the flag used when the cookie was set.
        samesite: Must match the attribute used when the cookie was set.

    """
    response.delete_cookie(
        key=cookie_name,
        httponly=True,
        secure=secure,
        samesite=samesite,
    )
