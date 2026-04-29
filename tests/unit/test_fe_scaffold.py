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


class TestAppNoAuth:
    def test_app_renders_router_provider_directly_without_auth(self) -> None:
        # Without auth, App mounts RouterProvider unconditionally.
        # The Shell (root route) handles whatever views exist; an
        # empty resources dict produces a router with just the
        # index route -- no "No views configured" message anymore.
        out = _files(ProjectConfig())
        app = out["src/App.tsx"]

        assert "<RouterProvider router={router} />" in app
        assert "AuthProvider" not in app
        assert 'import { router } from "./router"' in app


class TestAppWithAuth:
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

    def test_renders_loading_then_router_then_login(self) -> None:
        out = _files(self._cfg())
        app = out["src/App.tsx"]

        # Auth gating moved into TSR (#47): App threads ``useAuth()``
        # into ``RouterProvider`` context and shows a PageLoader
        # while auth resolves.  Login is now a TSR route, not an
        # App-level branch.
        assert 'auth.status === "loading"' in app
        assert "<PageLoader" in app
        assert "context={{ auth }}" in app or "context={ { auth } }" in app
        assert "<RouterProvider" in app
        # Shell still mounts inside the router tree, not here.
        assert "<Shell />" not in app


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
        # The Shell is the root route layout; the Outlet renders
        # whichever resource list the user navigated to.  Resource
        # list components are imported by the router, not the Shell.
        assert "<Outlet />" in shell
        assert (
            'import { ProjectsList } from "./projects/ProjectsList"'
            not in shell
        )

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
        # The hand-rolled avatar+name+role+sign-out block is replaced
        # by glaze's UserMenu primitive (#29).
        assert "<UserMenu" in shell
        assert "onSignOut" in shell
        # `Sign out` literal disappears -- UserMenu owns the label.
        assert "Sign out" not in shell

    def test_nav_items_use_router_navigation(self) -> None:
        # The active state is derived from the current location and
        # the onPress hands off to TanStack Router instead of toggling
        # local state.  No useState<View> anywhere.
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

        assert "useState<View>" not in shell
        assert "useLocation" in shell
        assert "useRouter" in shell
        # router.navigate now resets search via ``search: {}`` so
        # cross-resource clicks don't carry over filter/sort/etc.
        assert 'to: "/tasks"' in shell
        assert 'to: "/projects"' in shell
        assert "search: {}" in shell
        assert 'location.pathname.startsWith("/tasks")' in shell
        assert 'location.pathname.startsWith("/projects")' in shell
