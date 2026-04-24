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

import datetime
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, Literal, Protocol

import jwt
from fastapi import Cookie, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

DEFAULT_TOKEN_TTL = datetime.timedelta(minutes=30)
"""Lifetime stamped onto tokens when the caller does not set ``exp``."""

Source = Literal["bearer", "cookie"]
"""Where a session token may travel.  See module docstring for semantics."""

SameSite = Literal["lax", "strict", "none"]


class SessionStore(Protocol):
    """Server-side hook pair that turns the stateless flow stateful.

    Implement this on the consumer side to layer DB-backed
    revocation (deny-list), opaque session records, or anything
    else that needs per-token server state.  Two methods:

    * :meth:`is_revoked` -- called by :func:`session_auth`'s
      generated dep after JWT verification; returning ``True``
      rejects the request with HTTP 401 ``"Session revoked"``.
    * :meth:`revoke` -- called by the generated logout handler
      before :func:`clear_session`; marks the session dead so
      the next :meth:`is_revoked` call returns ``True``.

    Both methods are async so the store can hit a database
    without blocking the event loop.  In-memory implementations
    just declare ``async def`` and return immediately -- Python
    awaits on non-suspending coroutines cheaply.

    The store receives the whole :class:`~pydantic.BaseModel`
    session so it can key on whichever field is stable
    (typically ``jti``).  The consumer's ``Session`` is expected
    to carry that identity claim; ``ingot.auth`` itself stays
    agnostic about the shape.
    """

    async def is_revoked(self, session: BaseModel) -> bool:
        """Return ``True`` if *session* has been revoked."""
        ...

    async def revoke(self, session: BaseModel) -> None:
        """Mark *session* as revoked.  Should be idempotent."""
        ...


def _unauthorized() -> HTTPException:
    """Build a 401 for the "no valid token" case."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _revoked() -> HTTPException:
    """Build a 401 for the "token was valid but deny-listed" case."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session revoked",
        headers={"WWW-Authenticate": "Bearer"},
    )


_ALLOWED_SOURCES: tuple[Source, ...] = ("bearer", "cookie")


def _require_cookie_name(
    sources: Sequence[Source], cookie_name: str | None
) -> None:
    """Raise ``ValueError`` if the cookie source is used without a name."""
    if "cookie" in sources and cookie_name is None:
        msg = "cookie_name is required when 'cookie' is in sources"
        raise ValueError(msg)


def _validate_session_args(
    sources: Sequence[Source],
    token_url: str | None,
    cookie_name: str | None,
) -> None:
    """Validate the ``(sources, token_url, cookie_name)`` triple.

    Factored out of :func:`session_auth` so its body stays focused
    on building the dep.  The checks are runtime-only because the
    :data:`Source` ``Literal`` keeps typed call sites safe already.
    """
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
    return jwt.encode(claims, os.environ[secret_env], algorithm=algorithm)


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

    When *store* is supplied the dep also calls
    :meth:`SessionStore.is_revoked` after validation and raises
    HTTP 401 ``"Session revoked"`` if the store says so.  Use this
    to layer DB-backed deny-lists on top of the JWT without
    writing a wrapper dep on the consumer side.

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
        store: Optional server-side session store.  When supplied
            the dep consults it and rejects revoked sessions.

    Returns:
        An async dependency that yields a *schema* instance or
        raises HTTP 401.

    Raises:
        ValueError: If *sources* is empty, contains unknown values,
            or is missing the url/cookie-name required for a
            configured source.

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
    _require_cookie_name(sources, cookie_name)
    if "cookie" in sources:
        response.delete_cookie(
            key=cookie_name,  # type: ignore[arg-type]
            httponly=True,
            secure=cookie_secure,
            samesite=cookie_samesite,
        )
    return {"ok": True}
