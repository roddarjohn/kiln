"""Tests for :class:`fe.operations.scaffold.Scaffold`.

The scaffold op emits ``src/App.tsx``, ``src/Shell.tsx``
(conditional on shell config), and ``src/api/client.ts``.  The
shape of these files depends on which sections of the project
config are populated -- these tests assert each branch.
"""

from __future__ import annotations

from fe.config import (
    AuthConfig,
    NavItem,
    ProjectConfig,
    ResourceConfig,
    ResourceLabel,
    ShellConfig,
)
from fe.target import target as fe_target
from foundry.pipeline import generate


def _files(cfg: ProjectConfig) -> dict[str, str]:
    return {f.path: f.content for f in generate(cfg, fe_target)}


# ---------------------------------------------------------------------------
# Always-on outputs
# ---------------------------------------------------------------------------


class TestAlwaysEmitted:
    def test_api_client_emitted_even_without_auth(self) -> None:
        out = _files(ProjectConfig())

        assert "src/api/client.ts" in out
        client = out["src/api/client.ts"]
        # No auth -> client.ts is a no-op so it doesn't accidentally
        # attach a stale token from another app on the origin.
        assert "client.setConfig" not in client
        assert 'TOKEN_KEY = "' not in client

    def test_app_tsx_emitted(self) -> None:
        out = _files(ProjectConfig())

        assert "src/App.tsx" in out
        app = out["src/App.tsx"]
        assert "export default function App" in app
        # ToastRegion is part of the standard scaffold so toasts
        # raised by mutations always have a render target.
        assert "<ToastRegion />" in app


class TestApiClientWithAuth:
    def test_token_key_is_wired_into_client(self) -> None:
        cfg = ProjectConfig(
            auth=AuthConfig(
                login_fn="loginFn",
                validate_fn="validateFn",
                logout_fn="logoutFn",
                token_key="my-app:token",  # noqa: S106
            ),
        )
        out = _files(cfg)
        client = out["src/api/client.ts"]

        assert 'TOKEN_KEY = "my-app:token"' in client
        assert "client.setConfig" in client
        assert "window.localStorage.getItem(TOKEN_KEY)" in client


# ---------------------------------------------------------------------------
# App.tsx orchestration shape
# ---------------------------------------------------------------------------


class TestAppNoAuthNoShell:
    def test_renders_message_when_no_views(self) -> None:
        out = _files(ProjectConfig())
        app = out["src/App.tsx"]

        assert "AuthProvider" not in app
        assert "<Shell />" not in app
        assert "No views configured." in app

    def test_renders_first_resource_view_when_no_shell(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                ),
            },
        )
        out = _files(cfg)
        app = out["src/App.tsx"]

        assert "<ProjectsList />" in app
        assert 'import { ProjectsList } from "./projects/ProjectsList"' in app


class TestAppWithShellNoAuth:
    def test_renders_shell_directly(self) -> None:
        cfg = ProjectConfig(
            shell=ShellConfig(
                brand="kiln-sample",
                nav=[NavItem(label="Projects", view="projects")],
            ),
        )
        out = _files(cfg)
        app = out["src/App.tsx"]

        assert "<Shell />" in app
        assert "AuthProvider" not in app
        assert "AuthGate" not in app


class TestAppWithAuthAndShell:
    def _cfg(self) -> ProjectConfig:
        return ProjectConfig(
            shell=ShellConfig(
                brand="kiln-sample",
                nav=[NavItem(label="Projects", view="projects")],
            ),
            auth=AuthConfig(
                login_fn="createTokenV1AuthTokenPost",
                validate_fn="readSessionV1AuthTokenGet",
                logout_fn="logoutV1AuthTokenLogoutPost",
            ),
        )

    def test_wraps_in_auth_provider_with_typed_generics(self) -> None:
        out = _files(self._cfg())
        app = out["src/App.tsx"]

        assert "<AuthProvider<Session, LoginCredentials>" in app, (
            "AuthProvider should be parameterised with the configured types"
        )

    def test_passes_callbacks_from_api_auth(self) -> None:
        out = _files(self._cfg())
        app = out["src/App.tsx"]

        assert "validate={validate}" in app
        assert "login={login}" in app
        assert "logout={logout}" in app
        assert 'import { login, logout, validate } from "./api/auth"' in app

    def test_carries_storage_choice(self) -> None:
        out = _files(self._cfg())
        app = out["src/App.tsx"]

        assert 'storage="localStorage"' in app

    def test_renders_loading_then_login_then_shell(self) -> None:
        out = _files(self._cfg())
        app = out["src/App.tsx"]

        # Three branches: loading, authenticated -> Shell, else Login.
        assert 'auth.status === "loading"' in app
        assert 'auth.status === "authenticated"' in app
        assert "<Shell />" in app
        assert "<Login />" in app


# ---------------------------------------------------------------------------
# Shell.tsx
# ---------------------------------------------------------------------------


class TestShellEmission:
    def test_no_shell_config_skips_file(self) -> None:
        out = _files(ProjectConfig())
        assert "src/Shell.tsx" not in out

    def test_shell_emitted_with_brand_and_nav(self) -> None:
        cfg = ProjectConfig(
            shell=ShellConfig(
                brand="kiln-sample",
                nav=[
                    NavItem(label="Projects", view="projects"),
                    NavItem(label="Tasks", view="tasks"),
                ],
            ),
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                ),
                "tasks": ResourceConfig(
                    label=ResourceLabel(singular="Task", plural="Tasks"),
                    list_item_type="TaskListItem",
                ),
            },
        )
        out = _files(cfg)
        shell = out["src/Shell.tsx"]

        assert "AppShell" in shell
        assert "kiln-sample" in shell
        assert "Projects" in shell
        assert "Tasks" in shell
        # Each resource imports its list component.
        assert 'import { ProjectsList } from "./projects/ProjectsList"' in shell
        assert 'import { TasksList } from "./tasks/TasksList"' in shell

    def test_user_menu_omitted_without_auth(self) -> None:
        cfg = ProjectConfig(
            shell=ShellConfig(
                brand="kiln-sample",
                nav=[NavItem(label="Projects", view="projects")],
                user_menu=True,
            ),
        )
        out = _files(cfg)
        shell = out["src/Shell.tsx"]

        # No auth -> we have no session to render, so user_menu is
        # forced off regardless of the config knob.
        assert "useAuth" not in shell
        assert "Sign out" not in shell

    def test_user_menu_present_when_auth_and_user_menu_enabled(self) -> None:
        cfg = ProjectConfig(
            shell=ShellConfig(
                brand="kiln-sample",
                nav=[NavItem(label="Projects", view="projects")],
                user_menu=True,
            ),
            auth=AuthConfig(
                login_fn="loginFn",
                validate_fn="validateFn",
                logout_fn="logoutFn",
            ),
        )
        out = _files(cfg)
        shell = out["src/Shell.tsx"]

        assert "useAuth" in shell
        assert "useSession" in shell
        assert "Sign out" in shell

    def test_default_view_is_first_nav_item(self) -> None:
        cfg = ProjectConfig(
            shell=ShellConfig(
                brand="X",
                nav=[
                    NavItem(label="Tasks", view="tasks"),
                    NavItem(label="Projects", view="projects"),
                ],
            ),
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="P",
                ),
                "tasks": ResourceConfig(
                    label=ResourceLabel(singular="Task", plural="Tasks"),
                    list_item_type="T",
                ),
            },
        )
        out = _files(cfg)
        shell = out["src/Shell.tsx"]

        assert 'useState<View>("tasks")' in shell
