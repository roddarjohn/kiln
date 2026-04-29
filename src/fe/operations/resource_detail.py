"""Project-scope op: per-resource ``{Pascal}Detail.tsx``.

Conditional on a resource declaring both:

- ``get_fn`` -- the openapi-ts SDK function for the get-by-id
  endpoint, and
- ``detail`` -- a :class:`fe.config.DetailConfig` carrying the
  section breakdown and any header actions.

Detail is a TSR route at ``/<key>/$id``.  The component reads
``id`` from ``useParams``, fetches the resource through the
auto-generated ``*Options`` helper from the openapi-ts
react-query plugin, and suspends via ``useSuspenseQuery`` so the
route's ``pendingComponent`` (a glaze ``<PageLoader>``) handles
loading.  Errors propagate to the route's ``errorComponent``.

The header carries a back ``<Link>`` (``onPress`` runs
``router.history.back()``) plus optional ``Edit`` / per-action
buttons that navigate to the matching sibling routes.  Sections
render as ``<DataList>`` (or a user-supplied component).
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


def _humanize(field: str) -> str:
    return " ".join(p[:1].upper() + p[1:] for p in field.split("_"))


class _SectionContext(TypedDict):
    title: str | None
    fields: list[dict[str, str]]
    component: str | None


class _ActionContext(TypedDict):
    name: str
    label: str
    component: str
    path: str  # ``/<key>/$id/<name>`` to navigate to


@operation("resource_detail", scope="project")
class ResourceDetail:
    """Emit a Detail page for every resource that declares one."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield ``{Pascal}Detail.tsx`` per resource with detail."""
        config = ctx.instance

        for key, resource in config.resources.items():
            if resource.detail is None or resource.get_fn is None:
                continue

            pascal = _pascal(key)
            sections: list[_SectionContext] = [
                {
                    "title": sec.title,
                    "fields": [
                        {"name": f, "label": _humanize(f)} for f in sec.fields
                    ],
                    "component": sec.component,
                }
                for sec in resource.detail.sections
            ]

            # Resolve action keys -> action component names.
            actions: list[_ActionContext] = []

            for name in resource.detail.actions:
                action = resource.actions.get(name)

                if action is None:
                    continue

                actions.append(
                    {
                        "name": name,
                        "label": action.label,
                        "component": (f"{pascal}{_pascal(name)}Action"),
                        "path": f"/{key}/$id/{name}",
                    },
                )

            has_update = (
                resource.update is not None
                and resource.update_fn is not None
                and resource.get_fn is not None
            )

            id_prefix = "/_app" if config.auth is not None else ""

            yield StaticFile(
                path=f"src/{key}/{pascal}Detail.tsx",
                template="src/resource/Detail.tsx.j2",
                context={
                    "key": key,
                    "pascal": pascal,
                    "label_singular": resource.label.singular,
                    "label_plural": resource.label.plural,
                    "resource_type": resource.resource_type,
                    "get_fn": resource.get_fn,
                    "sections": sections,
                    "actions": actions,
                    "list_path": f"/{key}",
                    "detail_path": f"/{key}/$id",
                    "detail_route_id": f"{id_prefix}/{key}/$id",
                    "update_path": (f"/{key}/$id/edit" if has_update else None),
                    "has_update": has_update,
                    "title_field": resource.detail.title_field,
                    "subtitle_field": resource.detail.subtitle_field,
                },
            )
