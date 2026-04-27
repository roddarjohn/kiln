"""JWT auth primitives for kiln-generated FastAPI projects.

A session is a Pydantic model dumped into JWT claims.  Tokens
travel over one or both of two *sources*:

* ``"bearer"`` -- ``Authorization`` header; API clients.
* ``"cookie"`` -- ``httpOnly`` cookie; browser frontends (out of
  reach of JS so XSS can't steal it).

The signing secret lives in an env var (caller-named, typically
``JWT_SECRET``) so generated source never embeds a key.
"""

# NOTE: ``session_auth`` and the transport ``extract_dep`` helpers
# below build inner functions annotated with
# ``Annotated[..., Depends(<closure-local>)]``.  pydantic's
# ``TypeAdapter`` calls ``typing.get_type_hints`` against those
# inner functions when FastAPI builds the OpenAPI schema; closure
# locals aren't in ``__globals__``, so any stringified annotation
# (PEP 563) fails to resolve and 500s the schema build.  PEP 749's
# default deferred-but-lazy evaluation in 3.14 keeps annotations as
# real objects, preserving the closure scope -- but only as long as
# nothing forces them back to strings.  ``collections.abc`` must
# therefore be imported at runtime (not under ``TYPE_CHECKING``) so
# the same closure-local resolution can find ``Awaitable``,
# ``Callable``, and ``Sequence`` at request time.

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


class LoginResponse(BaseModel):
    """OAuth2-shaped login body for the bearer case."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 -- not a secret


class OkResponse(BaseModel):
    """Minimal ack body for cookie-only login and every logout."""

    ok: Literal[True] = True


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

    Subclasses register themselves against a :data:`Source` value
    via :data:`_TRANSPORTS`; adding a third source (e.g. a header
    carrying an API key) means writing a subclass and dropping an
    entry in that dict -- no changes to the public functions.
    """

    @classmethod
    def from_config(cls, **kwargs: Any) -> _Transport:
        """Build an instance from the loose config kwargs."""
        raise NotImplementedError

    def extract_dep(self) -> Callable[..., Awaitable[str | None]]:
        raise NotImplementedError

    def emit(
        self,
        response: Response,
        token: str,
        ttl: datetime.timedelta,
    ) -> LoginResponse | None:
        """Write the token to this transport on login.

        Returning a :class:`LoginResponse` makes it the response
        body (the bearer case); returning ``None`` means the
        transport lives in headers only (the cookie case).
        """
        raise NotImplementedError

    def clear(self, response: Response) -> None:
        raise NotImplementedError


class _BearerTransport(_Transport):
    """``Authorization: Bearer`` header.

    ``token_url`` only surfaces to OpenAPI via
    :class:`OAuth2PasswordBearer`; runtime extraction reads the
    header.  ``issue_session`` / ``clear_session`` don't call
    :meth:`extract_dep` so they pass ``None``.
    """

    def __init__(self, token_url: str | None) -> None:
        self._oauth = (
            OAuth2PasswordBearer(tokenUrl=token_url, auto_error=False)
            if token_url is not None
            else None
        )

    @classmethod
    def from_config(cls, **kwargs: Any) -> _BearerTransport:
        return cls(kwargs.get("token_url"))

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
    ) -> LoginResponse | None:
        return LoginResponse(access_token=token)

    def clear(self, response: Response) -> None:  # noqa: ARG002
        # Bearer logout is client-side -- clients discard the token.
        return


class _CookieTransport(_Transport):
    """``httpOnly`` cookie.

    ``secure`` / ``samesite`` must match between :meth:`emit` and
    :meth:`clear` -- browsers refuse to overwrite an existing
    cookie when either attribute differs.
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

    @classmethod
    def from_config(cls, **kwargs: Any) -> _CookieTransport:
        name = kwargs.get("cookie_name")
        if name is None:
            msg = "cookie_name is required when 'cookie' is in sources"
            raise ValueError(msg)
        return cls(
            name,
            secure=kwargs.get("cookie_secure", True),
            samesite=kwargs.get("cookie_samesite", "lax"),
        )

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
    ) -> LoginResponse | None:
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


_TRANSPORTS: dict[Source, type[_Transport]] = {
    "bearer": _BearerTransport,
    "cookie": _CookieTransport,
}


async def _no_token() -> str | None:
    """Stand-in extractor for a source that isn't configured.

    Lets :func:`session_auth` expose a uniform ``(bearer, cookie)``
    signature regardless of which sources are actually in use.
    FastAPI doesn't add a security scheme for a plain
    ``Depends(_no_token)``, so OpenAPI still advertises only the
    configured sources.
    """
    return None


def _build_transports(
    sources: Sequence[Source],
    **config: Any,
) -> dict[Source, _Transport]:
    """Build transport instances keyed on source name.

    Dispatches through :data:`_TRANSPORTS` so each subclass owns
    its own config-extraction rules via
    :meth:`_Transport.from_config`.  ``session_auth`` still guards
    the bearer-needs-``token_url`` case separately because
    ``issue_session`` / ``clear_session`` build bearer transports
    without one (they don't call :meth:`extract_dep`).
    """
    unknown = [src for src in sources if src not in _TRANSPORTS]
    if unknown:
        msg = f"unknown source(s): {sorted(set(unknown))}"
        raise ValueError(msg)
    if not sources:
        msg = f"sources must contain at least one of {sorted(_TRANSPORTS)}"
        raise ValueError(msg)
    return {src: _TRANSPORTS[src].from_config(**config) for src in sources}


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


def session_auth[SessionT: BaseModel](
    schema: type[SessionT],
    sources: Sequence[Source],
    *,
    secret_env: str,
    algorithm: str,
    token_url: str | None = None,
    cookie_name: str | None = None,
    store: SessionStore | None = None,
) -> Callable[..., Awaitable[SessionT]]:
    """Build a FastAPI dep that yields a validated *schema* instance.

    The returned callable takes one parameter per supported
    transport; configured sources plug in their real extractors,
    unconfigured ones get a no-token shim (returns ``None``).
    The first non-``None`` token wins.  Claims parse through
    :meth:`~pydantic.BaseModel.model_validate` so handlers get the
    full model, not a raw dict.

    *store*, when supplied, turns every authenticated request into
    a deny-list check -- avoids a wrapper dep on the consumer side.
    """
    transports = _build_transports(
        sources, token_url=token_url, cookie_name=cookie_name
    )
    if "bearer" in sources and token_url is None:
        msg = "token_url is required when 'bearer' is in sources"
        raise ValueError(msg)

    bearer_transport = transports.get("bearer")
    cookie_transport = transports.get("cookie")
    bearer_ext = (
        bearer_transport.extract_dep() if bearer_transport else _no_token
    )
    cookie_ext = (
        cookie_transport.extract_dep() if cookie_transport else _no_token
    )

    async def resolve(token: str | None) -> SessionT:
        if token is None:
            raise _unauthorized()
        claims = decode_jwt(token, secret_env=secret_env, algorithm=algorithm)
        session = schema.model_validate(claims)
        if store is not None and await store.is_revoked(session):
            raise _revoked()
        return session

    async def get_session(
        bearer: Annotated[str | None, Depends(bearer_ext)] = None,
        cookie: Annotated[str | None, Depends(cookie_ext)] = None,
    ) -> SessionT:
        return await resolve(bearer or cookie)

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
) -> LoginResponse | OkResponse:
    """Mint a JWT and emit it to every configured transport.

    Collapses ``session is None`` (validate rejected the creds)
    to HTTP 401 so login handlers stay one-liners.  The JWT is
    encoded once and reused across transports; when both are
    configured the cookie and response body carry the same token.
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

    body: LoginResponse | OkResponse = OkResponse()
    for transport in transports.values():
        emitted = transport.emit(response, token, ttl)
        if emitted is not None:
            body = emitted
    return body


def clear_session(
    response: Response,
    *,
    sources: Sequence[Source],
    cookie_name: str | None = None,
    cookie_secure: bool = True,
    cookie_samesite: SameSite = "lax",
) -> OkResponse:
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
    for transport in transports.values():
        transport.clear(response)
    return OkResponse()
