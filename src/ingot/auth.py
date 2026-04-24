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


class _Transport:
    """One way a JWT rides the request/response pair.

    Each subclass owns three operations:

    * :meth:`extract_dep` -- FastAPI dep returning the token from
      this transport, or ``None`` if absent.
    * :meth:`emit` -- write a freshly-minted token to the
      transport on login.  Returns a dict to become the response
      body, or ``None`` when the transport lives entirely in
      headers.
    * :meth:`clear` -- tear the transport down on logout.
    """

    def extract_dep(self) -> Callable[..., Awaitable[str | None]]:
        raise NotImplementedError

    def emit(
        self,
        response: Response,
        token: str,
        ttl: datetime.timedelta,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    def clear(self, response: Response) -> None:
        raise NotImplementedError


class _BearerTransport(_Transport):
    """``Authorization: Bearer`` header.

    Holds ``token_url`` only to feed :class:`OAuth2PasswordBearer`
    (surfaces to OpenAPI for Swagger's Authorize button).  Runtime
    extraction doesn't actually GET that URL, so ``issue_session``
    and ``clear_session`` pass ``None``.
    """

    def __init__(self, token_url: str | None) -> None:
        self._oauth = (
            OAuth2PasswordBearer(tokenUrl=token_url, auto_error=False)
            if token_url is not None
            else None
        )

    def extract_dep(self) -> Callable[..., Awaitable[str | None]]:
        if self._oauth is None:  # pragma: no cover -- session_auth pre-guards
            msg = "token_url is required for bearer extraction"
            raise ValueError(msg)
        oauth = self._oauth

        async def _extract(
            bearer: Annotated[str | None, Depends(oauth)] = None,
        ) -> str | None:
            return bearer

        return _extract

    def emit(
        self,
        response: Response,  # noqa: ARG002
        token: str,
        ttl: datetime.timedelta,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        return {"access_token": token, "token_type": "bearer"}

    def clear(self, response: Response) -> None:  # noqa: ARG002
        # Bearer logout is client-side — clients discard the token.
        return


class _CookieTransport(_Transport):
    """``httpOnly`` cookie.

    ``secure`` / ``samesite`` must match between :meth:`emit` and
    :meth:`clear` — browsers refuse to overwrite an existing
    cookie when those attributes differ.
    """

    def __init__(
        self,
        name: str,
        *,
        secure: bool = True,
        samesite: SameSite = "lax",
    ) -> None:
        self._name = name
        self._secure = secure
        self._samesite = samesite

    def extract_dep(self) -> Callable[..., Awaitable[str | None]]:
        name = self._name

        async def _extract(
            cookie: Annotated[str | None, Cookie(alias=name)] = None,
        ) -> str | None:
            return cookie

        return _extract

    def emit(
        self,
        response: Response,
        token: str,
        ttl: datetime.timedelta,
    ) -> dict[str, Any] | None:
        response.set_cookie(
            key=self._name,
            value=token,
            max_age=int(ttl.total_seconds()),
            httponly=True,
            secure=self._secure,
            samesite=self._samesite,
        )
        return None

    def clear(self, response: Response) -> None:
        response.delete_cookie(
            key=self._name,
            httponly=True,
            secure=self._secure,
            samesite=self._samesite,
        )


_ALLOWED_SOURCES: tuple[Source, ...] = ("bearer", "cookie")


def _build_transports(
    sources: Sequence[Source],
    *,
    token_url: str | None = None,
    cookie_name: str | None = None,
    cookie_secure: bool = True,
    cookie_samesite: SameSite = "lax",
) -> list[_Transport]:
    """Turn the public ``sources`` spec into transport instances.

    Validates the args needed by the transports chosen; does *not*
    enforce ``token_url`` for bearer, since ``issue_session`` /
    ``clear_session`` construct bearer transports without one.
    :func:`session_auth` guards that separately.
    """
    unknown = [s for s in sources if s not in _ALLOWED_SOURCES]
    if unknown:
        msg = f"unknown source(s): {sorted(set(unknown))}"
        raise ValueError(msg)
    if not sources:
        msg = "sources must contain at least one of 'bearer' or 'cookie'"
        raise ValueError(msg)
    out: list[_Transport] = []
    for src in sources:
        if src == "bearer":
            out.append(_BearerTransport(token_url))
        else:
            if cookie_name is None:
                msg = "cookie_name is required when 'cookie' is in sources"
                raise ValueError(msg)
            out.append(
                _CookieTransport(
                    cookie_name,
                    secure=cookie_secure,
                    samesite=cookie_samesite,
                )
            )
    return out


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


def session_auth[T: BaseModel](  # noqa: C901 -- FastAPI needs static per-combo signatures
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
    transport; the first token that's present wins.  Claims parse
    through :meth:`~pydantic.BaseModel.model_validate` so handlers
    get the full model, not a raw dict.

    *store*, when supplied, turns every authenticated request into
    a deny-list check -- avoids a wrapper dep on the consumer side.
    """
    transports = _build_transports(
        sources, token_url=token_url, cookie_name=cookie_name
    )
    if "bearer" in sources and token_url is None:
        msg = "token_url is required when 'bearer' is in sources"
        raise ValueError(msg)

    bearer_ext: Callable[..., Awaitable[str | None]] | None = None
    cookie_ext: Callable[..., Awaitable[str | None]] | None = None
    for t in transports:
        if isinstance(t, _BearerTransport):
            bearer_ext = t.extract_dep()
        else:
            cookie_ext = t.extract_dep()

    async def resolve(token: str | None) -> T:
        if token is None:
            raise _unauthorized()
        claims = decode_jwt(token, secret_env=secret_env, algorithm=algorithm)
        session = schema.model_validate(claims)
        if store is not None and await store.is_revoked(session):
            raise _revoked()
        return session

    # The three signatures below differ only in which transport
    # parameters FastAPI should inject; the bodies all funnel
    # through ``resolve``.
    if bearer_ext is not None and cookie_ext is not None:

        async def get_session(
            bearer: Annotated[str | None, Depends(bearer_ext)] = None,
            cookie: Annotated[str | None, Depends(cookie_ext)] = None,
        ) -> T:
            return await resolve(bearer or cookie)

    elif bearer_ext is not None:

        async def get_session(  # type: ignore[misc]
            bearer: Annotated[str | None, Depends(bearer_ext)] = None,
        ) -> T:
            return await resolve(bearer)

    else:
        assert cookie_ext is not None  # noqa: S101 -- _build_transports guarantees

        async def get_session(  # type: ignore[misc]
            cookie: Annotated[str | None, Depends(cookie_ext)] = None,
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
    """Mint a JWT and emit it to every configured transport.

    Collapses ``session is None`` (validate rejected the creds)
    to HTTP 401 so login handlers stay one-liners.  The JWT is
    encoded once and reused across transports; when both are
    configured the cookie and response body carry the same token.

    Returns ``{"access_token", "token_type"}`` when bearer is in
    *sources*, else ``{"ok": True}``.
    """
    transports = _build_transports(
        sources,
        cookie_name=cookie_name,
        cookie_secure=cookie_secure,
        cookie_samesite=cookie_samesite,
    )

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

    body: dict[str, Any] = {"ok": True}
    for t in transports:
        result = t.emit(response, token, ttl)
        if result is not None:
            body = result
    return body


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
    transports = _build_transports(
        sources,
        cookie_name=cookie_name,
        cookie_secure=cookie_secure,
        cookie_samesite=cookie_samesite,
    )
    for t in transports:
        t.clear(response)
    return {"ok": True}
