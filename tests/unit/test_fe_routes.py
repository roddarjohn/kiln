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
        out = _files(cfg)["src/router.tsx"]

        assert "projectsRoute" in out
        assert 'path: "/projects"' in out
        assert "ProjectsList" in out
        assert 'import { ProjectsList } from "./projects/ProjectsList"' in out

    def test_resource_without_list_fn_skipped(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(),  # no list_fn
            },
        )
        out = _files(cfg)["src/router.tsx"]

        assert "ProjectsList" not in out
        assert "projectsRoute" not in out

    def test_index_route_uses_first_resource_component(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(list_fn="listFn"),
                "tasks": _resource(list_fn="listTasksFn"),
            },
        )
        out = _files(cfg)["src/router.tsx"]

        # Index route renders the first resource's list so a fresh
        # visit to "/" doesn't dump the user on a blank page.
        assert "indexRoute" in out
        assert 'path: "/"' in out
        assert "component: ProjectsList" in out

    def test_route_tree_includes_every_route(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(list_fn="listProjectsFn"),
                "tasks": _resource(list_fn="listTasksFn"),
            },
        )
        out = _files(cfg)["src/router.tsx"]

        assert "rootRoute.addChildren([" in out
        assert "indexRoute," in out
        assert "projectsRoute," in out
        assert "tasksRoute," in out
