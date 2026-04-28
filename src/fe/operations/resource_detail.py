"""Project-scope op: per-resource ``{Pascal}Detail.tsx``.

Conditional on a resource declaring both:

* ``get_fn`` -- the openapi-ts SDK function for the get-by-id
  endpoint, and
* ``detail`` -- a :class:`fe.config.DetailConfig` carrying the
  section breakdown and any header actions.

The detail page fetches the resource via React Query, renders
each configured section (fields rendered as label / value pairs
or a user-supplied component), and exposes header buttons for
any actions referenced by ``detail.actions``.

The component is exported as ``{Pascal}Detail`` and accepts:

* ``id: string`` -- the resource id to fetch.
* ``close: () => void`` -- called when the page wants to be
  dismissed (e.g. from a Drawer wrapper in the list page).

The list op (:mod:`fe.operations.resource_list`) opens this
component in a glaze Drawer when ``list.row_click == "detail"``.
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
                    },
                )

            yield StaticFile(
                path=f"src/{key}/{pascal}Detail.tsx",
                template="src/resource/Detail.tsx.j2",
                context={
                    "key": key,
                    "pascal": pascal,
                    "label_singular": resource.label.singular,
                    "resource_type": resource.resource_type,
                    "get_fn": resource.get_fn,
                    "sections": sections,
                    "actions": actions,
                },
            )
