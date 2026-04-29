"""Project-scope op: ``App.tsx``, ``Shell.tsx``, ``api/client.ts``.

The scaffold op reads the whole :class:`~fe.config.ProjectConfig`
and emits the small set of always-present plumbing files that
glue everything else together:

- ``src/App.tsx`` -- top-level orchestration.  Mounts
  ``<AuthProvider>`` when auth is configured and threads the
  live ``useAuth()`` value into the TSR ``RouterProvider`` as
  context so route-level ``beforeLoad`` hooks can gate access.
  No ``AuthGate`` -- auth redirection lives in
  :mod:`fe.operations.routes` (``rootRoute.beforeLoad`` bounces
  unauthenticated requests to ``/login``).
- ``src/Shell.tsx`` -- AppShell + sidebar nav, mounted as the
  authenticated layout's component.  Wraps the ``<Outlet>`` in
  a ``<PageContent>`` plus a ``<Suspense>`` boundary so route
  ``useSuspenseQuery`` calls render the glaze ``<PageLoader>``
  fallback.
- ``src/api/client.ts`` -- configures the openapi-ts client to
  attach the bearer token from the AuthProvider's storage.
  Always emitted (it's a one-line no-op when auth is absent).

Each output is overwrite-on-regenerate -- this op owns these
paths.  Hand edits will be lost on the next ``just generate``;
that's the trade for keeping the orchestration in lockstep with
the kiln config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from fe.config import ProjectConfig
    from foundry.engine import BuildContext


class _ResourceView(TypedDict):
    key: str
    label_singular: str
    label_plural: str
    list_component: str
    list_module: str


class _NavEntry(TypedDict):
    label: str
    view: str
    component: str | None
    icon: str | None  # lucide-react icon name


def _pascal(key: str) -> str:
    """Convert a snake_case / kebab-case key to PascalCase.

    Used to derive component file/symbol names from the dict
    keys in :attr:`ProjectConfig.resources`.  ``"projects"`` ->
    ``"Projects"``, ``"audit_logs"`` -> ``"AuditLogs"``.
    """
    parts = [p for p in key.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


@operation("scaffold", scope="project")
class Scaffold:
    """Emit App.tsx + Shell.tsx + api/client.ts."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield the scaffold files for the configured project."""
        config = ctx.instance

        # Pre-compute per-resource view metadata so templates stay
        # simple: directory + import path + component symbol.
        resource_views: list[_ResourceView] = [
            {
                "key": key,
                "label_singular": resource.label.singular,
                "label_plural": resource.label.plural,
                "list_component": f"{_pascal(key)}List",
                "list_module": f"./{key}/{_pascal(key)}List",
            }
            for key, resource in config.resources.items()
        ]
        view_lookup: dict[str, _ResourceView] = {
            v["key"]: v for v in resource_views
        }

        # Resolve nav -> view component, defaulting to the first
        # resource view if the user didn't reference a known one.
        nav: list[_NavEntry] = []

        if config.shell is not None:
            for item in config.shell.nav:
                view_meta = view_lookup.get(item.view)
                nav.append(
                    {
                        "label": item.label,
                        "view": item.view,
                        "component": (
                            view_meta["list_component"] if view_meta else None
                        ),
                        "icon": item.icon,
                    },
                )

        default_view: str | None = nav[0]["view"] if nav else None

        # When there's no shell, App.tsx still needs *something* to
        # render at the root.  Fall back to the first resource's
        # list page so a minimum-viable config (just `resources: {}`)
        # boots instead of showing the placeholder text.
        if default_view is None and resource_views:
            default_view = resource_views[0]["key"]

        # ---- src/api/client.ts ------------------------------------
        yield StaticFile(
            path="src/api/client.ts",
            template="src/api/client.ts.j2",
            context={
                "token_key": (
                    config.auth.token_key if config.auth else "auth:token"
                ),
                "auth_enabled": config.auth is not None,
            },
        )

        # ---- src/Shell.tsx ---------------------------------------
        if config.shell is not None:
            # Collect the unique set of lucide-react icon names so
            # the template can emit a single named-import line.
            icon_names = sorted(
                {item["icon"] for item in nav if item["icon"] is not None},
            )

            yield StaticFile(
                path="src/Shell.tsx",
                template="src/Shell.tsx.j2",
                context={
                    "brand": config.shell.brand,
                    "nav": nav,
                    "default_view": default_view,
                    "user_menu": (
                        config.shell.user_menu and config.auth is not None
                    ),
                    "auth": config.auth,
                    "resource_views": resource_views,
                    "icon_names": icon_names,
                },
            )

        # ---- src/App.tsx -----------------------------------------
        yield StaticFile(
            path="src/App.tsx",
            template="src/App.tsx.j2",
            context={
                "auth": config.auth,
                "has_shell": config.shell is not None,
                "first_view_component": (
                    view_lookup[default_view]["list_component"]
                    if default_view and default_view in view_lookup
                    else None
                ),
                "first_view_module": (
                    view_lookup[default_view]["list_module"]
                    if default_view and default_view in view_lookup
                    else None
                ),
            },
        )
