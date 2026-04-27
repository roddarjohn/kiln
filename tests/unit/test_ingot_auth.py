"""Tests for ingot.auth."""

import datetime
from typing import Annotated
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ingot.auth import (
    LoginResponse,
    OkResponse,
    clear_session,
    decode_jwt,
    encode_jwt,
    issue_session,
    session_auth,
)

SECRET = "test-secret-at-least-32-bytes-long-for-hs256"  # noqa: S105
ENV_VAR = "INGOT_TEST_JWT_SECRET"
ALG = "HS256"


class Session(BaseModel):
    """Test session model — typical minimal shape."""

    sub: str
    roles: list[str] = []


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, SECRET)


# -------------------------------------------------------------------
# encode_jwt / decode_jwt
# -------------------------------------------------------------------


class TestEncodeDecode:
    """Round-trip and failure modes for the primitive helpers."""

    def test_round_trip(self) -> None:
        token = encode_jwt({"sub": "alice"}, secret_env=ENV_VAR, algorithm=ALG)
        decoded = decode_jwt(token, secret_env=ENV_VAR, algorithm=ALG)
        assert decoded["sub"] == "alice"
        assert "exp" in decoded

    def test_encode_does_not_mutate_input(self) -> None:
        payload = {"sub": "alice"}
        encode_jwt(payload, secret_env=ENV_VAR, algorithm=ALG)
        assert payload == {"sub": "alice"}

    def test_encode_respects_caller_exp(self) -> None:
        exp = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        token = encode_jwt(
            {"sub": "a", "exp": exp},
            secret_env=ENV_VAR,
            algorithm=ALG,
        )
        decoded = decode_jwt(token, secret_env=ENV_VAR, algorithm=ALG)
        assert decoded["exp"] == int(exp.timestamp())

    def test_decode_raises_401_on_garbage(self) -> None:
        with pytest.raises(HTTPException) as exc:
            decode_jwt("not-a-jwt", secret_env=ENV_VAR, algorithm=ALG)
        assert exc.value.status_code == 401
        assert exc.value.headers == {"WWW-Authenticate": "Bearer"}

    def test_decode_raises_401_when_secret_env_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_VAR, raising=False)
        token = jwt.encode({"sub": "a"}, SECRET, algorithm=ALG)
        with pytest.raises(HTTPException) as exc:
            decode_jwt(token, secret_env=ENV_VAR, algorithm=ALG)
        assert exc.value.status_code == 401


# -------------------------------------------------------------------
# session_auth — the dep factory
#
# The returned callable takes one argument per configured source
# (FastAPI fills them at runtime via Depends + Cookie).  We call
# the dep directly rather than going through TestClient, which
# would pull in httpx.
# -------------------------------------------------------------------


def _token(**claims: object) -> str:
    return encode_jwt(
        {"sub": "alice", **claims},  # type: ignore[arg-type]
        secret_env=ENV_VAR,
        algorithm=ALG,
    )


class TestSessionAuthValidation:
    """Argument validation at factory-build time."""

    def test_empty_sources_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            session_auth(Session, [], secret_env=ENV_VAR, algorithm=ALG)

    def test_unknown_source_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown source"):
            session_auth(
                Session,
                ["bearer", "smoke-signals"],  # type: ignore[list-item]
                secret_env=ENV_VAR,
                algorithm=ALG,
            )

    def test_bearer_requires_token_url(self) -> None:
        with pytest.raises(ValueError, match="token_url"):
            session_auth(Session, ["bearer"], secret_env=ENV_VAR, algorithm=ALG)

    def test_cookie_requires_cookie_name(self) -> None:
        with pytest.raises(ValueError, match="cookie_name"):
            session_auth(Session, ["cookie"], secret_env=ENV_VAR, algorithm=ALG)


class TestSessionAuthBearer:
    """Bearer-only source."""

    def _dep(self):
        return session_auth(
            Session,
            ["bearer"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
        )

    async def test_returns_schema_instance(self) -> None:
        dep = self._dep()
        session = await dep(bearer=_token())
        assert isinstance(session, Session)
        assert session.sub == "alice"

    async def test_missing_bearer_is_401(self) -> None:
        dep = self._dep()
        with pytest.raises(HTTPException) as exc:
            await dep(bearer=None)
        assert exc.value.status_code == 401

    async def test_invalid_bearer_is_401(self) -> None:
        dep = self._dep()
        with pytest.raises(HTTPException) as exc:
            await dep(bearer="garbage")
        assert exc.value.status_code == 401


class TestSessionAuthCookie:
    """Cookie-only source."""

    def _dep(self):
        return session_auth(
            Session,
            ["cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            cookie_name="session",
        )

    async def test_returns_schema_instance(self) -> None:
        dep = self._dep()
        session = await dep(cookie=_token())
        assert session.sub == "alice"

    async def test_missing_cookie_is_401(self) -> None:
        dep = self._dep()
        with pytest.raises(HTTPException) as exc:
            await dep(cookie=None)
        assert exc.value.status_code == 401


class TestSessionAuthBoth:
    """Both sources — picks the first token that's present."""

    def _dep(self):
        return session_auth(
            Session,
            ["bearer", "cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
            cookie_name="session",
        )

    async def test_bearer_wins_when_both_present(self) -> None:
        dep = self._dep()
        # bearer carries a specific claim the cookie doesn't, so we
        # can tell which one the dep decoded.
        bearer_tok = _token(marker="from-bearer")
        cookie_tok = _token(marker="from-cookie")
        session = await dep(bearer=bearer_tok, cookie=cookie_tok)
        decoded = decode_jwt(bearer_tok, secret_env=ENV_VAR, algorithm=ALG)
        assert decoded["marker"] == "from-bearer"
        assert isinstance(session, Session)

    async def test_falls_back_to_cookie(self) -> None:
        dep = self._dep()
        session = await dep(bearer=None, cookie=_token())
        assert session.sub == "alice"

    async def test_all_empty_is_401(self) -> None:
        dep = self._dep()
        with pytest.raises(HTTPException) as exc:
            await dep(bearer=None, cookie=None)
        assert exc.value.status_code == 401


# -------------------------------------------------------------------
# issue_session
# -------------------------------------------------------------------


class TestIssueSession:
    """Login-endpoint helper."""

    def test_bearer_returns_oauth2_shape(self) -> None:
        response = MagicMock()
        out = issue_session(
            response,
            Session(sub="alice"),
            sources=["bearer"],
            secret_env=ENV_VAR,
            algorithm=ALG,
        )
        assert isinstance(out, LoginResponse)
        assert out.token_type == "bearer"  # noqa: S105
        decoded = decode_jwt(
            out.access_token, secret_env=ENV_VAR, algorithm=ALG
        )
        assert decoded["sub"] == "alice"
        response.set_cookie.assert_not_called()

    def test_cookie_only_sets_cookie_and_returns_ok(self) -> None:
        response = MagicMock()
        out = issue_session(
            response,
            Session(sub="alice"),
            sources=["cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            cookie_name="session",
            cookie_secure=True,
            cookie_samesite="strict",
        )
        assert isinstance(out, OkResponse)
        response.set_cookie.assert_called_once()
        kwargs = response.set_cookie.call_args.kwargs
        assert kwargs["key"] == "session"
        assert kwargs["httponly"] is True
        assert kwargs["secure"] is True
        assert kwargs["samesite"] == "strict"

    def test_both_sources_emit_same_token(self) -> None:
        """One encode, emitted in both places — not two tokens."""
        response = MagicMock()
        out = issue_session(
            response,
            Session(sub="alice"),
            sources=["bearer", "cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            cookie_name="session",
        )
        assert isinstance(out, LoginResponse)
        cookie_value = response.set_cookie.call_args.kwargs["value"]
        assert cookie_value == out.access_token

    def test_none_session_raises_401(self) -> None:
        response = MagicMock()
        with pytest.raises(HTTPException) as exc:
            issue_session(
                response,
                None,
                sources=["bearer"],
                secret_env=ENV_VAR,
                algorithm=ALG,
            )
        assert exc.value.status_code == 401
        response.set_cookie.assert_not_called()

    def test_cookie_without_name_rejected(self) -> None:
        response = MagicMock()
        with pytest.raises(ValueError, match="cookie_name"):
            issue_session(
                response,
                Session(sub="alice"),
                sources=["cookie"],
                secret_env=ENV_VAR,
                algorithm=ALG,
            )


# -------------------------------------------------------------------
# clear_session
# -------------------------------------------------------------------


class TestClearSession:
    """Logout helper."""

    def test_cookie_source_deletes_cookie(self) -> None:
        response = MagicMock()
        out = clear_session(
            response,
            sources=["cookie"],
            cookie_name="session",
            cookie_secure=False,
            cookie_samesite="lax",
        )
        assert isinstance(out, OkResponse)
        response.delete_cookie.assert_called_once_with(
            key="session",
            httponly=True,
            secure=False,
            samesite="lax",
        )

    def test_bearer_only_is_noop(self) -> None:
        response = MagicMock()
        out = clear_session(response, sources=["bearer"])
        assert isinstance(out, OkResponse)
        response.delete_cookie.assert_not_called()

    def test_cookie_without_name_rejected(self) -> None:
        response = MagicMock()
        with pytest.raises(ValueError, match="cookie_name"):
            clear_session(response, sources=["cookie"])


# -------------------------------------------------------------------
# session_auth + SessionStore
# -------------------------------------------------------------------


class _FakeStore:
    """Minimal :class:`ingot.auth.SessionStore` for tests.

    Backs the deny-list with a plain ``set`` of ``jti`` values.
    Tracks method call counts so we can assert the dep consulted
    ``is_revoked`` even when the session wasn't in the deny-list.
    """

    def __init__(self, revoked: set[str] | None = None) -> None:
        self.revoked: set[str] = set(revoked or ())
        self.is_revoked_calls = 0
        self.revoke_calls = 0

    async def is_revoked(self, session: BaseModel) -> bool:
        self.is_revoked_calls += 1
        sub = session.model_dump().get("sub", "")
        return sub in self.revoked

    async def revoke(self, session: BaseModel) -> None:
        self.revoke_calls += 1
        sub = session.model_dump().get("sub", "")
        self.revoked.add(sub)


class TestSessionAuthStore:
    """Deny-list hook on top of each transport mode."""

    async def test_bearer_rejects_revoked(self) -> None:
        store = _FakeStore(revoked={"alice"})
        dep = session_auth(
            Session,
            ["bearer"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
            store=store,
        )
        with pytest.raises(HTTPException) as exc:
            await dep(bearer=_token())
        assert exc.value.status_code == 401
        assert exc.value.detail == "Session revoked"
        assert store.is_revoked_calls == 1

    async def test_cookie_rejects_revoked(self) -> None:
        store = _FakeStore(revoked={"alice"})
        dep = session_auth(
            Session,
            ["cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            cookie_name="session",
            store=store,
        )
        with pytest.raises(HTTPException) as exc:
            await dep(cookie=_token())
        assert exc.value.status_code == 401

    async def test_both_sources_rejects_revoked(self) -> None:
        store = _FakeStore(revoked={"alice"})
        dep = session_auth(
            Session,
            ["bearer", "cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
            cookie_name="session",
            store=store,
        )
        with pytest.raises(HTTPException):
            await dep(bearer=_token(), cookie=None)

    async def test_store_consulted_even_when_not_revoked(self) -> None:
        """Deny-list check runs on every authenticated request."""
        store = _FakeStore()
        dep = session_auth(
            Session,
            ["bearer"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
            store=store,
        )
        session = await dep(bearer=_token())
        assert isinstance(session, Session)
        assert store.is_revoked_calls == 1


# -------------------------------------------------------------------
# session_auth + FastAPI OpenAPI schema build
#
# Regression guard: ``session_auth`` returns a closure whose
# parameters are annotated ``Annotated[..., Depends(<closure-local>)]``.
# Under PEP 563 string annotations the closure locals fall out of
# scope, ``typing.get_type_hints`` raises NameError, and pydantic's
# OpenAPI schema build 500s.  These tests would have caught
# https://github.com/roddarjohn/kiln/pull/46.
# -------------------------------------------------------------------


def _app_with(dep) -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    def me(session: Annotated[Session, Depends(dep)]) -> Session:
        return session

    return app


class TestSessionAuthOpenApi:
    """``app.openapi()`` must succeed for every supported source mix."""

    def test_bearer_only(self) -> None:
        dep = session_auth(
            Session,
            ["bearer"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
        )
        schema = _app_with(dep).openapi()
        assert "/me" in schema["paths"]

    def test_cookie_only(self) -> None:
        dep = session_auth(
            Session,
            ["cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            cookie_name="session",
        )
        schema = _app_with(dep).openapi()
        assert "/me" in schema["paths"]

    def test_both_sources(self) -> None:
        dep = session_auth(
            Session,
            ["bearer", "cookie"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
            cookie_name="session",
        )
        schema = _app_with(dep).openapi()
        assert "/me" in schema["paths"]

    async def test_no_store_skips_check(self) -> None:
        """With ``store=None`` the dep never attempts a lookup."""
        store = _FakeStore()
        dep = session_auth(
            Session,
            ["bearer"],
            secret_env=ENV_VAR,
            algorithm=ALG,
            token_url="/auth/token",  # noqa: S106
            store=None,
        )
        await dep(bearer=_token())
        assert store.is_revoked_calls == 0
