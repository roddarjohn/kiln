"""JWT auth primitives for kiln-generated FastAPI projects.

A session is carried in a JWT whose claims are a dump of a
caller-supplied Pydantic model.  The token can travel through one
or more *sources* — today the choices are:

* ``"bearer"`` — the ``Authorization: Bearer`` header, read through
  :class:`fastapi.security.OAuth2PasswordBearer`.  Typical for API
  clients, CLIs, mobile apps.
* ``"cookie"`` — an ``httpOnly`` cookie.  Typical for browser
  frontends; keeps the token out of reach of JS so XSS can't steal
  it.

Configure any non-empty combination:

* ``["bearer"]`` — login returns OAuth2-shaped JSON; session dep
  reads the header.
* ``["cookie"]`` — login sets the cookie; session dep reads it.
* ``["bearer", "cookie"]`` — login does both (so a single endpoint
  serves both web and API clients); session dep accepts either.

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
    from collections.abc import Awaitable, Callable, Sequence

    from fastapi import Response

DEFAULT_TOKEN_TTL = datetime.timedelta(minutes=30)
"""Lifetime stamped onto tokens when the caller does not set ``exp``."""

Source = Literal["bearer", "cookie"]
"""Where a session token may travel.  See module docstring for semantics."""

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


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


_ALLOWED_SOURCES: frozenset[Source] = frozenset({"bearer", "cookie"})


def _validate_session_sources(
    sources: Sequence[Source],
    *,
    token_url: str | None,
    cookie_name: str | None,
) -> None:
    """Validate ``sources`` arguments for :func:`session_auth`."""
    if not sources:
        msg = "sources must contain at least one of 'bearer' or 'cookie'"
        raise ValueError(msg)
    unknown = [s for s in sources if s not in _ALLOWED_SOURCES]
    if unknown:
        msg = f"unknown source(s): {sorted(set(unknown))}"
        raise ValueError(msg)
    if "bearer" in sources and token_url is None:
        msg = "token_url is required when 'bearer' is in sources"
        raise ValueError(msg)
    if "cookie" in sources and cookie_name is None:
        msg = "cookie_name is required when 'cookie' is in sources"
        raise ValueError(msg)


def _bearer_dep[T: BaseModel](
    schema: type[T],
    *,
    token_url: str,
    secret_env: str,
    algorithm: str,
) -> Callable[..., Awaitable[T]]:
    oauth = OAuth2PasswordBearer(tokenUrl=token_url, auto_error=False)

    async def get_session(
        bearer: Annotated[str | None, Depends(oauth)] = None,
    ) -> T:
        if bearer is None:
            raise _unauthorized()
        claims = decode_jwt(bearer, secret_env=secret_env, algorithm=algorithm)
        return schema.model_validate(claims)

    return get_session


def _cookie_dep[T: BaseModel](
    schema: type[T],
    *,
    cookie_name: str,
    secret_env: str,
    algorithm: str,
) -> Callable[..., Awaitable[T]]:
    async def get_session(
        cookie: Annotated[str | None, Cookie(alias=cookie_name)] = None,
    ) -> T:
        if cookie is None:
            raise _unauthorized()
        claims = decode_jwt(cookie, secret_env=secret_env, algorithm=algorithm)
        return schema.model_validate(claims)

    return get_session


def _bearer_or_cookie_dep[T: BaseModel](
    schema: type[T],
    *,
    token_url: str,
    cookie_name: str,
    secret_env: str,
    algorithm: str,
) -> Callable[..., Awaitable[T]]:
    oauth = OAuth2PasswordBearer(tokenUrl=token_url, auto_error=False)

    async def get_session(
        bearer: Annotated[str | None, Depends(oauth)] = None,
        cookie: Annotated[str | None, Cookie(alias=cookie_name)] = None,
    ) -> T:
        token = bearer or cookie
        if token is None:
            raise _unauthorized()
        claims = decode_jwt(token, secret_env=secret_env, algorithm=algorithm)
        return schema.model_validate(claims)

    return get_session


def session_auth[T: BaseModel](
    schema: type[T],
    sources: Sequence[Source],
    *,
    secret_env: str,
    algorithm: str,
    token_url: str | None = None,
    cookie_name: str | None = None,
) -> Callable[..., Awaitable[T]]:
    """Build a FastAPI dependency that yields a validated session.

    The returned callable is suitable for ``Depends(...)``.  Its
    signature carries one parameter per configured source; FastAPI
    extracts each (bearer via :class:`OAuth2PasswordBearer` with
    ``auto_error=False``, cookie via :class:`fastapi.Cookie`), and
    the first token that's present wins.  When every source is
    empty the dep raises HTTP 401.

    Validated JWT claims are parsed into *schema* via
    :meth:`pydantic.BaseModel.model_validate` before the dep
    returns, so handlers type ``session: Session`` and get the full
    model — not a raw dict.

    Args:
        schema: Pydantic model describing the session payload.
        sources: Ordered list of transports to accept.  Must be
            non-empty and a subset of ``{"bearer", "cookie"}``.
        secret_env: Environment variable holding the signing secret.
        algorithm: Expected signing algorithm.
        token_url: Path of the login endpoint, required when
            ``"bearer"`` is in *sources*.  Surfaced to OpenAPI via
            :class:`OAuth2PasswordBearer`.
        cookie_name: Name of the cookie carrying the JWT, required
            when ``"cookie"`` is in *sources*.

    Returns:
        An async dependency that yields a *schema* instance or
        raises HTTP 401.

    Raises:
        ValueError: If *sources* is empty, contains unknown values,
            or is missing the url/cookie-name required for a
            configured source.

    """
    _validate_session_sources(
        sources, token_url=token_url, cookie_name=cookie_name
    )

    use_bearer = "bearer" in sources
    use_cookie = "cookie" in sources

    if use_bearer and use_cookie:
        assert token_url is not None  # noqa: S101 -- _validate guarantees
        assert cookie_name is not None  # noqa: S101
        return _bearer_or_cookie_dep(
            schema,
            token_url=token_url,
            cookie_name=cookie_name,
            secret_env=secret_env,
            algorithm=algorithm,
        )
    if use_bearer:
        assert token_url is not None  # noqa: S101
        return _bearer_dep(
            schema,
            token_url=token_url,
            secret_env=secret_env,
            algorithm=algorithm,
        )
    assert cookie_name is not None  # noqa: S101
    return _cookie_dep(
        schema,
        cookie_name=cookie_name,
        secret_env=secret_env,
        algorithm=algorithm,
    )


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
    """Mint a session JWT and emit it to each configured source.

    Collapses the "validate returned None" case into a 401 so login
    handlers stay one-liners.  The token is encoded *once* and
    reused across sources: when ``["bearer", "cookie"]`` is
    configured, the same JWT is both set as a cookie and returned
    in the OAuth2-shaped body.

    Args:
        response: FastAPI response that receives the Set-Cookie
            header when ``"cookie"`` is in *sources*.
        session: Validated session model, or ``None`` when
            credentials did not match.
        sources: Which transports to emit to.  Same rules as
            :func:`session_auth`.
        secret_env: Environment variable holding the signing secret.
        algorithm: JWT signing algorithm.
        ttl: Token lifetime; also used as cookie ``max_age``.
        cookie_name: Name of the cookie, required when ``"cookie"``
            is in *sources*.
        cookie_secure: ``Secure`` flag for the cookie.
        cookie_samesite: SameSite attribute for the cookie.

    Returns:
        When ``"bearer"`` is in *sources*:
        ``{"access_token": <jwt>, "token_type": "bearer"}``.
        Otherwise ``{"ok": True}``.

    Raises:
        HTTPException: 401 if *session* is ``None``.
        ValueError: If *sources* is empty or ``"cookie"`` is in
            *sources* without ``cookie_name``.

    """
    if not sources:
        msg = "sources must be non-empty"
        raise ValueError(msg)

    if "cookie" in sources and cookie_name is None:
        msg = "cookie_name is required when 'cookie' is in sources"
        raise ValueError(msg)

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
    """Emit a logout for each configured source.

    For ``"cookie"``, a ``Set-Cookie`` header is emitted that deletes
    the cookie; the ``secure``/``samesite`` values must match what
    :func:`issue_session` used or browsers refuse to overwrite the
    existing cookie.

    For ``"bearer"``, logout is client-side (clients discard the
    token); this function has nothing to do but acknowledge.

    Args:
        response: FastAPI response that carries the Set-Cookie
            header that deletes the cookie.
        sources: Which transports to clear.
        cookie_name: Name of the cookie to clear, required when
            ``"cookie"`` is in *sources*.
        cookie_secure: Must match the flag used at issuance.
        cookie_samesite: Must match the attribute used at issuance.

    Returns:
        ``{"ok": True}``.

    Raises:
        ValueError: If ``"cookie"`` is in *sources* without
            ``cookie_name``.

    """
    if "cookie" in sources:
        if cookie_name is None:
            msg = "cookie_name is required when 'cookie' is in sources"
            raise ValueError(msg)
        response.delete_cookie(
            key=cookie_name,
            httponly=True,
            secure=cookie_secure,
            samesite=cookie_samesite,
        )
    return {"ok": True}
