"""Tests for :class:`fe.operations.auth.Auth`.

The auth op emits ``src/api/auth.ts`` and
``src/auth/Login.tsx`` only when the project config sets an
``auth`` section.  These tests cover both branches and verify
the configured operation IDs / type names land verbatim in the
generated TypeScript.
"""

from __future__ import annotations

from fe.config import AuthConfig, ProjectConfig
from fe.target import target as fe_target
from foundry.pipeline import generate


def _files(cfg: ProjectConfig) -> dict[str, str]:
    return {f.path: f.content for f in generate(cfg, fe_target)}


# ---------------------------------------------------------------------------
# Conditional emission
# ---------------------------------------------------------------------------


class TestAuthConditional:
    def test_no_auth_section_skips_files(self) -> None:
        out = _files(ProjectConfig())

        assert "src/api/auth.ts" not in out
        assert "src/auth/Login.tsx" not in out

    def test_auth_section_emits_both_files(self) -> None:
        out = _files(
            ProjectConfig(
                auth=AuthConfig(
                    login_fn="loginFn",
                    validate_fn="validateFn",
                    logout_fn="logoutFn",
                ),
            ),
        )

        assert "src/api/auth.ts" in out
        assert "src/auth/Login.tsx" in out


# ---------------------------------------------------------------------------
# api/auth.ts content
# ---------------------------------------------------------------------------


class TestApiAuth:
    def _out(self) -> str:
        cfg = ProjectConfig(
            auth=AuthConfig(
                login_fn="createTokenV1AuthTokenPost",
                validate_fn="readSessionV1AuthTokenGet",
                logout_fn="logoutV1AuthTokenLogoutPost",
                session_type="MySession",
                credentials_type="MyCreds",
            ),
        )
        return _files(cfg)["src/api/auth.ts"]

    def test_imports_configured_sdk_fns(self) -> None:
        out = self._out()

        assert "createTokenV1AuthTokenPost" in out
        assert "readSessionV1AuthTokenGet" in out
        assert "logoutV1AuthTokenLogoutPost" in out
        assert 'from "../_generated/sdk.gen"' in out

    def test_imports_configured_types(self) -> None:
        out = self._out()

        assert "MySession" in out
        assert "MyCreds" in out
        assert 'from "../_generated/types.gen"' in out

    def test_login_handles_token_extraction(self) -> None:
        out = self._out()

        # The bearer token is extracted before the session call so
        # we don't depend on AuthProvider having persisted it yet.
        assert "access_token" in out
        assert "Bearer ${accessToken}" in out

    def test_validate_swallows_errors(self) -> None:
        out = self._out()

        # validate() should never reject -- it returns null on
        # failure so AuthProvider transitions to "unauthenticated"
        # rather than "error" for routine 401s.
        assert "return null" in out
        assert "} catch {" in out

    def test_logout_calls_configured_fn(self) -> None:
        out = self._out()
        assert "await logoutV1AuthTokenLogoutPost()" in out


# ---------------------------------------------------------------------------
# auth/Login.tsx content
# ---------------------------------------------------------------------------


class TestLoginPage:
    def _out(self, **overrides: object) -> str:
        cfg = ProjectConfig(
            auth=AuthConfig(
                login_fn="loginFn",
                validate_fn="validateFn",
                logout_fn="logoutFn",
                **overrides,  # type: ignore[arg-type]
            ),
        )
        return _files(cfg)["src/auth/Login.tsx"]

    def test_imports_glaze_form_components(self) -> None:
        out = self._out()

        assert "TextField" in out
        assert "Button" in out
        assert "Card" in out
        assert 'from "@roddarjohn/glaze"' in out

    def test_uses_use_auth_with_typed_generics(self) -> None:
        out = self._out(
            session_type="Session", credentials_type="LoginCredentials"
        )

        assert "useAuth<Session, LoginCredentials>()" in out

    def test_default_credentials_fields_render_username_password(self) -> None:
        out = self._out()

        # Default ``credentials_fields = ["username", "password"]``
        # must produce two TextFields, one of which is type=password.
        assert 'label="Username"' in out
        assert 'label="Password"' in out
        assert 'type="password"' in out

    def test_custom_credentials_fields(self) -> None:
        out = self._out(credentials_fields=["email", "password"])

        assert 'label="Email"' in out
        assert 'label="Password"' in out
        assert "username" not in out

    def test_login_hint_rendered_when_set(self) -> None:
        without = self._out()
        assert "description=" not in without

        with_hint = self._out(login_hint="Try alice / wonderland")
        assert "Try alice / wonderland" in with_hint

    def test_calls_auth_login_with_credentials(self) -> None:
        out = self._out()

        assert "await auth.login({" in out
        assert "username," in out
        assert "password," in out
