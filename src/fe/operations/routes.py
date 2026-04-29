"""Project-scope op: top-level + per-resource route trees.

The kiln fe target makes TanStack Router a hard requirement, so
this op always emits ``src/router.tsx``.  It also emits one
``src/<key>/routes.tsx`` per resource that exports a
``make<Pascal>Routes(parent)`` factory mirroring the BE's
``APIRouter`` sub-app pattern -- adding a new resource means
one new file, never editing the central router.

Per-resource route shape:

* ``/<key>``                      -> ``{Pascal}List``
* ``/<key>/new``                  -> ``Create{Pascal}Form``
* ``/<key>/$id``                  -> ``{Pascal}Detail``
* ``/<key>/$id/edit``             -> ``Update{Pascal}Form``
* ``/<key>/$id/<action>``         -> ``{Pascal}{Action}Action`` (one per action)

The router.tsx file declares:

* ``rootRoute`` -- pathless root, just an ``<Outlet/>``.
* ``loginRoute`` at ``/login`` -- public, no Shell chrome.
* ``appLayoutRoute`` -- pathless layout that renders ``<Shell>``
  and runs ``beforeLoad`` to redirect unauthenticated requests
  to ``/login``.  All resource trees mount under this layout.

Auth state is injected as TSR context via ``createRouter({
context })``; ``beforeLoad`` reads ``context.auth`` to gate
access.  Public routes (login) sit outside the layout so they
never trigger the redirect.

The list route carries a typed ``validateSearch`` for filters,
sort, and pagination -- the only state that should round-trip
through the URL on a list page.  Detail / form / action pages
read only the ``$id`` URL param.
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


class _FilterSearchField(TypedDict):
    """A single filter field expressed for ``validateSearch``."""

    id: str
    type: str  # "text" | "boolean" | "select"
    options: list[str]


class _ActionRoute(TypedDict):
    """One ``/<key>/$id/<name>`` action route."""

    name: str
    path: str
    component: str
    module: str


class _RouteContext(TypedDict):
    """Per-resource context driving the router template."""

    key: str
    pascal: str
    list_path: str
    list_component: str
    list_module: str
    detail_path: str | None
    detail_component: str | None
    detail_module: str | None
    create_path: str | None
    create_component: str | None
    create_module: str | None
    update_path: str | None
    update_component: str | None
    update_module: str | None
    action_routes: list[_ActionRoute]
    has_pagination: bool
    has_sortable: bool
    filters: list[_FilterSearchField]


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

            has_detail = (
                resource.detail is not None and resource.get_fn is not None
            )
            has_create = (
                resource.create is not None and resource.create_fn is not None
            )
            has_update = (
                resource.update is not None
                and resource.update_fn is not None
                and resource.get_fn is not None
            )

            # Surface an action as a route when it is invoked from
            # either a row button (``row_action``) or the detail
            # page (listed in ``detail.actions``).
            detail_action_names = (
                set(resource.detail.actions) if resource.detail else set()
            )
            action_routes: list[_ActionRoute] = []

            for name, action in resource.actions.items():
                if not (action.row_action or name in detail_action_names):
                    continue

                component = f"{pascal}{_pascal(name)}Action"
                action_routes.append(
                    {
                        "name": name,
                        "path": f"/{key}/$id/{name}",
                        "component": component,
                        "module": f"./actions/{component}",
                    },
                )

            filters: list[_FilterSearchField] = [
                {
                    "id": f.field,
                    "type": f.type,
                    "options": list(f.options),
                }
                for f in resource.list.filters
            ]

            routes.append(
                {
                    "key": key,
                    "pascal": pascal,
                    "list_path": f"/{key}",
                    "list_component": f"{pascal}List",
                    # Modules are imported from the per-resource
                    # ``routes.tsx`` (sibling of the surface files),
                    # so paths are relative to ``src/<key>/``.
                    "list_module": f"./{pascal}List",
                    "detail_path": (f"/{key}/$id" if has_detail else None),
                    "detail_component": (
                        f"{pascal}Detail" if has_detail else None
                    ),
                    "detail_module": (
                        f"./{pascal}Detail" if has_detail else None
                    ),
                    "create_path": (f"/{key}/new" if has_create else None),
                    "create_component": (
                        f"Create{pascal}Form" if has_create else None
                    ),
                    "create_module": (
                        f"./Create{pascal}Form" if has_create else None
                    ),
                    "update_path": (f"/{key}/$id/edit" if has_update else None),
                    "update_component": (
                        f"Update{pascal}Form" if has_update else None
                    ),
                    "update_module": (
                        f"./Update{pascal}Form" if has_update else None
                    ),
                    "action_routes": action_routes,
                    "has_pagination": resource.list.page_size is not None,
                    "has_sortable": any(
                        c.sortable for c in resource.list.columns
                    ),
                    "filters": filters,
                },
            )

        first_path = routes[0]["list_path"] if routes else "/"
        first_component = routes[0]["list_component"] if routes else None
        # Each search-helper function (asString / asNumber / etc)
        # is emitted only if at least one route's validateSearch
        # actually calls it -- otherwise tsc flags it as unused.
        needs_string = any(
            route["has_sortable"]
            or any(f["type"] in {"text", "select"} for f in route["filters"])
            for route in routes
        )
        needs_number = any(route["has_pagination"] for route in routes)
        needs_bool = any(
            f["type"] == "boolean" for route in routes for f in route["filters"]
        )
        needs_enum = any(
            route["has_sortable"]
            or any(f["type"] == "select" for f in route["filters"])
            for route in routes
        )

        yield StaticFile(
            path="src/router.tsx",
            template="src/router.tsx.j2",
            context={
                "routes": routes,
                "index_redirect": first_path,
                "first_component": first_component,
                "auth_enabled": config.auth is not None,
            },
        )

        # Per-resource routes.tsx -- mirrors the BE's APIRouter
        # sub-app pattern.  Each file exports a ``make<Pascal>Routes``
        # factory that takes the parent layout route and returns the
        # resource's child routes.  Adding a new resource is one new
        # file and a one-line import in router.tsx.
        for route in routes:
            yield StaticFile(
                path=f"src/{route['key']}/routes.tsx",
                template="src/resource/routes.tsx.j2",
                context={
                    "route": route,
                    "needs_string": route["has_sortable"]
                    or any(
                        f["type"] in {"text", "select"}
                        for f in route["filters"]
                    ),
                    "needs_number": route["has_pagination"],
                    "needs_bool": any(
                        f["type"] == "boolean" for f in route["filters"]
                    ),
                    "needs_enum": route["has_sortable"]
                    or any(f["type"] == "select" for f in route["filters"]),
                },
            )

        # Search-param helpers live in their own module so the
        # per-resource routes files don't each redeclare them.
        if needs_string or needs_number or needs_bool or needs_enum:
            yield StaticFile(
                path="src/_search.ts",
                template="src/_search.ts.j2",
                context={
                    "needs_string": needs_string,
                    "needs_number": needs_number,
                    "needs_bool": needs_bool,
                    "needs_enum": needs_enum,
                },
            )
