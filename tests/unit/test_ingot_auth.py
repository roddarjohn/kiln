"""Tests for ingot.auth."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException

from ingot.auth import (
    bearer_auth,
    clear_auth_cookie,
    cookie_auth,
    decode_jwt,
    encode_jwt,
    issue_bearer_token,
    set_auth_cookie,
)

SECRET = "test-secret-at-least-32-bytes-long-for-hs256"  # noqa: S105
ENV_VAR = "INGOT_TEST_JWT_SECRET"
ALG = "HS256"


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

    def test_decode_raises_401_on_wrong_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = jwt.encode(
            {"sub": "a"},
            "a-different-secret-that-is-also-long-enough",
            algorithm=ALG,
        )
        monkeypatch.setenv(ENV_VAR, SECRET)
        with pytest.raises(HTTPException) as exc:
            decode_jwt(token, secret_env=ENV_VAR, algorithm=ALG)
        assert exc.value.status_code == 401


# -------------------------------------------------------------------
# bearer_auth
#
# The factory returns an async dependency whose sole argument is the
# token string (FastAPI fills it at runtime from OAuth2PasswordBearer).
# We call it directly rather than going through TestClient, which
# would pull in httpx.
# -------------------------------------------------------------------


class TestBearerAuth:
    """Header-based dependency factory."""

    async def test_returns_decoded_payload(self) -> None:
        dep = bearer_auth(
            token_url="/auth/token",  # noqa: S106
            secret_env=ENV_VAR,
            algorithm=ALG,
        )
        token = encode_jwt({"sub": "alice"}, secret_env=ENV_VAR, algorithm=ALG)
        payload = await dep(token)
        assert payload["sub"] == "alice"

    async def test_invalid_token_raises_401(self) -> None:
        dep = bearer_auth(
            token_url="/auth/token",  # noqa: S106
            secret_env=ENV_VAR,
            algorithm=ALG,
        )
        with pytest.raises(HTTPException) as exc:
            await dep("garbage")
        assert exc.value.status_code == 401


# -------------------------------------------------------------------
# cookie_auth
# -------------------------------------------------------------------


class TestCookieAuth:
    """Cookie-based dependency factory."""

    async def test_returns_decoded_payload(self) -> None:
        dep = cookie_auth(
            cookie_name="session", secret_env=ENV_VAR, algorithm=ALG
        )
        token = encode_jwt({"sub": "alice"}, secret_env=ENV_VAR, algorithm=ALG)
        payload = await dep(token)
        assert payload["sub"] == "alice"

    async def test_missing_cookie_is_401(self) -> None:
        dep = cookie_auth(
            cookie_name="session", secret_env=ENV_VAR, algorithm=ALG
        )
        with pytest.raises(HTTPException) as exc:
            await dep(None)
        assert exc.value.status_code == 401
        assert exc.value.headers == {"WWW-Authenticate": "Bearer"}

    async def test_invalid_cookie_is_401(self) -> None:
        dep = cookie_auth(
            cookie_name="session", secret_env=ENV_VAR, algorithm=ALG
        )
        with pytest.raises(HTTPException) as exc:
            await dep("not-a-jwt")
        assert exc.value.status_code == 401


# -------------------------------------------------------------------
# issue_bearer_token / set_auth_cookie / clear_auth_cookie
# -------------------------------------------------------------------


class TestIssueBearerToken:
    """Login-endpoint helper for header transport."""

    def test_returns_oauth2_shape(self) -> None:
        out = issue_bearer_token(
            {"sub": "alice"}, secret_env=ENV_VAR, algorithm=ALG
        )
        assert out["token_type"] == "bearer"  # noqa: S105
        decoded = decode_jwt(
            out["access_token"], secret_env=ENV_VAR, algorithm=ALG
        )
        assert decoded["sub"] == "alice"

    def test_none_payload_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc:
            issue_bearer_token(None, secret_env=ENV_VAR, algorithm=ALG)
        assert exc.value.status_code == 401


class TestSetAuthCookie:
    """Login-endpoint helper for cookie transport."""

    def test_sets_cookie_with_expected_flags(self) -> None:
        response = MagicMock()
        set_auth_cookie(
            response,
            {"sub": "alice"},
            cookie_name="session",
            secret_env=ENV_VAR,
            algorithm=ALG,
            ttl=datetime.timedelta(minutes=15),
            secure=True,
            samesite="strict",
        )
        response.set_cookie.assert_called_once()
        kwargs = response.set_cookie.call_args.kwargs
        assert kwargs["key"] == "session"
        assert kwargs["httponly"] is True
        assert kwargs["secure"] is True
        assert kwargs["samesite"] == "strict"
        assert kwargs["max_age"] == 15 * 60
        decoded = decode_jwt(kwargs["value"], secret_env=ENV_VAR, algorithm=ALG)
        assert decoded["sub"] == "alice"

    def test_none_payload_raises_401(self) -> None:
        response = MagicMock()
        with pytest.raises(HTTPException) as exc:
            set_auth_cookie(
                response,
                None,
                cookie_name="session",
                secret_env=ENV_VAR,
                algorithm=ALG,
            )
        assert exc.value.status_code == 401
        response.set_cookie.assert_not_called()


class TestClearAuthCookie:
    """Logout helper."""

    def test_calls_delete_cookie(self) -> None:
        response = MagicMock()
        clear_auth_cookie(
            response,
            cookie_name="session",
            secure=False,
            samesite="lax",
        )
        response.delete_cookie.assert_called_once_with(
            key="session",
            httponly=True,
            secure=False,
            samesite="lax",
        )
