"""Project-scope op: per-resource custom action components.

For every action declared on a resource (``resources.*.actions``)
this op emits ``src/{key}/actions/{Pascal}{ActionPascal}Action.tsx``,
the form/dialog body wired to the action's openapi-ts SDK fn.

The list page (:mod:`fe.operations.resource_list`) is responsible
for the ``DialogTrigger`` / ``Dialog`` wrapper -- this op only
emits the inner content rendered by the dialog's
``({ close }) => ...`` render-prop.

Action components accept:

* ``item: {ListItemType}`` -- the row the action runs against.
  Object actions use ``item.id`` for the path param.
* ``close: () => void`` -- the dialog close callback supplied by
  glaze's ``DialogTrigger`` render-prop.

The body fields are state-driven ``TextField``s keyed off
``fields`` in the action config; a ``confirm_text`` renders as
text above the inputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


@operation("resource_action", scope="project")
class ResourceAction:
    """Emit one action component per (resource, action) pair."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield ``actions/{Component}.tsx`` files."""
        config = ctx.instance

        for key, resource in config.resources.items():
            if not resource.actions:
                continue

            pascal = _pascal(key)

            for action_name, action in resource.actions.items():
                component = f"{pascal}{_pascal(action_name)}Action"
                fields = [
                    {"name": f, "label": _humanize(f)} for f in action.fields
                ]

                # Cancel / success on an action page returns to
                # the detail (when one exists) or the list.
                has_detail = (
                    resource.detail is not None and resource.get_fn is not None
                )

                id_prefix = "/_app" if config.auth is not None else ""

                yield StaticFile(
                    path=f"src/{key}/actions/{component}.tsx",
                    template="src/resource/Action.tsx.j2",
                    context={
                        "key": key,
                        "pascal": pascal,
                        "list_item_type": resource.list_item_type,
                        "component": component,
                        "label": action.label,
                        "fn": action.fn,
                        "request_schema": action.request_schema,
                        "fields": fields,
                        "confirm_text": action.confirm_text,
                        "action_path": f"/{key}/$id/{action_name}",
                        "action_route_id": (
                            f"{id_prefix}/{key}/$id/{action_name}"
                        ),
                        "list_path": f"/{key}",
                        "detail_path": f"/{key}/$id",
                        "has_detail": has_detail,
                    },
                )
