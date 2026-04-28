"""Project-scope op: ``src/router.tsx`` (TanStack Router tree).

Always emitted -- the kiln fe target makes TanStack Router a hard
requirement so apps have URL-shareable views from day one.

The route tree is flat: a root route renders the ``<Shell>``
layout (sidebar + Outlet), and one child route per configured
resource maps ``/{key}`` to its ``{Pascal}List`` component.  An
index route at ``/`` renders the first resource's list so the
app boots into something useful.

Detail / form / action surfaces still live in Drawers and
Dialogs hosted by the list pages; the router only owns the
top-level resource navigation.  When we want URL-shareable
detail views (``/projects/$id``) that's a follow-up that adds
nested routes here.
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


def _pascal(key: str) -> str:
    parts = [p for p in key.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


class _RouteContext(TypedDict):
    key: str
    path: str
    list_component: str
    list_module: str


@operation("routes", scope="project")
class Routes:
    """Emit ``src/router.tsx`` with the TanStack Router tree."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield router.tsx covering every resource with a list view."""
        config = ctx.instance
        routes: list[_RouteContext] = []

        for key, resource in config.resources.items():
            if resource.list_fn is None:
                continue

            pascal = _pascal(key)
            routes.append(
                {
                    "key": key,
                    "path": f"/{key}",
                    "list_component": f"{pascal}List",
                    "list_module": f"./{key}/{pascal}List",
                },
            )

        first_path = routes[0]["path"] if routes else "/"
        first_component = routes[0]["list_component"] if routes else None

        yield StaticFile(
            path="src/router.tsx",
            template="src/router.tsx.j2",
            context={
                "routes": routes,
                "index_redirect": first_path,
                "first_component": first_component,
            },
        )
