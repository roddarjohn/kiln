"""Tests for :class:`fe.operations.routes.Routes`.

The router op emits ``src/router.tsx`` containing a TanStack
Router route tree: a root route rendering the Shell layout, an
index route, and one child route per resource that declares a
``list_fn``.
"""

from __future__ import annotations

from fe.config import (
    ProjectConfig,
    ResourceConfig,
    ResourceLabel,
)
from fe.target import target as fe_target
from foundry.pipeline import generate


def _files(cfg: ProjectConfig) -> dict[str, str]:
    return {f.path: f.content for f in generate(cfg, fe_target)}


def _resource(*, list_fn: str | None = None) -> ResourceConfig:
    return ResourceConfig(
        label=ResourceLabel(singular="Project", plural="Projects"),
        list_item_type="ProjectListItem",
        **({"list_fn": list_fn} if list_fn else {}),
    )


# ---------------------------------------------------------------------------
# Always-on emission
# ---------------------------------------------------------------------------


class TestEmission:
    def test_router_always_emitted(self) -> None:
        # Even with no resources, router.tsx exists -- the kiln fe
        # target makes TSR a hard requirement, so App.tsx can always
        # mount RouterProvider without conditional logic.
        out = _files(ProjectConfig())
        assert "src/router.tsx" in out

    def test_router_imports_tanstack_primitives(self) -> None:
        out = _files(ProjectConfig())["src/router.tsx"]

        assert "createRootRoute" in out
        assert "createRoute" in out
        assert "createRouter" in out
        assert 'from "@tanstack/react-router"' in out

    def test_router_exports_router(self) -> None:
        out = _files(ProjectConfig())["src/router.tsx"]
        assert "export const router = createRouter(" in out

    def test_module_register_declared_for_typed_links(self) -> None:
        # The `declare module` block tells TSR which router type
        # backs `<Link>` / `useRouter()` so paths get type-safe.
        out = _files(ProjectConfig())["src/router.tsx"]
        assert 'declare module "@tanstack/react-router"' in out
        assert "router: typeof router" in out

    def test_router_wires_default_error_and_not_found(self) -> None:
        # Errors bubbling out of any route render through a
        # glaze-styled EmptyState rather than TSR's raw default
        # so users see something readable.
        out = _files(ProjectConfig())["src/router.tsx"]

        assert "function RouterError" in out
        assert "function RouterNotFound" in out
        assert "defaultErrorComponent: RouterError" in out
        assert "defaultNotFoundComponent: RouterNotFound" in out
        # The root route also gets the same handler so the Shell
        # chrome stays mounted when a child throws.
        assert "errorComponent: RouterError" in out
        assert "notFoundComponent: RouterNotFound" in out


# ---------------------------------------------------------------------------
# Resource routes
# ---------------------------------------------------------------------------


class TestResourceRoutes:
    def test_resource_with_list_fn_gets_a_route(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(list_fn="listFn"),
            },
        )
        files = _files(cfg)
        router = files["src/router.tsx"]
        sub = files["src/projects/routes.tsx"]

        # router.tsx is a thin composer: it imports the resource's
        # ``make*Routes`` factory and mounts the returned subtree.
        assert "makeProjectsRoutes" in router
        assert (
            'import { makeProjectsRoutes } from "./projects/routes"' in router
        )
        # The actual routes (and their components) live in the
        # per-resource sub-app.
        assert 'path: "/projects"' in sub
        assert (
            'import { ProjectsList } from "./projects/ProjectsList"' in sub
            or 'import { ProjectsList } from "./ProjectsList"' in sub
        )

    def test_resource_without_list_fn_skipped(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(),  # no list_fn
            },
        )
        files = _files(cfg)
        router = files["src/router.tsx"]

        assert "makeProjectsRoutes" not in router
        assert "src/projects/routes.tsx" not in files

    def test_index_route_uses_first_resource_component(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(list_fn="listFn"),
                "tasks": _resource(list_fn="listTasksFn"),
            },
        )
        out = _files(cfg)["src/router.tsx"]

        # Index redirects to the first resource's list path so a
        # fresh visit to "/" lands the user on a real page (and
        # ``useSearch({ from: "/<key>" })`` inside the list page
        # finds an active match).
        assert "indexRoute" in out
        assert 'path: "/"' in out
        assert 'redirect({ to: "/projects"' in out

    def test_route_tree_includes_every_route(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(list_fn="listProjectsFn"),
                "tasks": _resource(list_fn="listTasksFn"),
            },
        )
        out = _files(cfg)["src/router.tsx"]

        # The tree mounts every per-resource sub-app's spread
        # of routes under the appLayoutRoute.
        assert "addChildren([" in out
        assert "indexRoute," in out
        assert "...projectsRoutes," in out
        assert "...tasksRoutes," in out
