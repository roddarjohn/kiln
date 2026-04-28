"""Project-scope op: per-resource ``{Resource}List.tsx``.

For every entry in :attr:`fe.config.ProjectConfig.resources` this
op emits one ``src/{key}/{Pascal}List.tsx`` -- a page composing
glaze's ``Table`` + ``EmptyState`` + ``Spinner`` against the
openapi-ts-generated React-Query hook for the resource's list
endpoint.

The page renders:

* The configured columns, with ``"text"`` or ``"badge"`` cells.
* A toolbar ``New {label}`` button when the resource declares
  ``create_fn`` and ``"create"`` is in ``list.toolbar_actions``
  (the button opens the corresponding ``Create{Pascal}Form``
  emitted by :mod:`fe.operations.resource_form` inside a glaze
  ``DrawerTrigger`` -> ``Drawer``).
* A row-level Delete button when ``delete_fn`` is set and
  ``"delete"`` is in ``list.row_actions``.
* Per-row buttons for any custom ``actions`` whose
  ``row_action`` flag is True; each opens the matching
  ``{Pascal}Action`` modal/drawer emitted by
  :mod:`fe.operations.resource_action`.

The list query's queryKey is ``[<key>, "list"]`` so mutations
elsewhere can invalidate it with a single call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from fe.config import (
        ActionConfig,
        ColumnSpec,
        FilterSpec,
        ProjectConfig,
    )
    from foundry.engine import BuildContext


# Default operator per filter type when the user doesn't override.
_DEFAULT_OP: dict[str, str] = {
    "text": "contains",
    "boolean": "eq",
    "select": "eq",
}


def _pascal(key: str) -> str:
    parts = [p for p in key.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


class _ColumnContext(TypedDict):
    field: str
    label: str
    display: str
    is_first: bool
    sortable: bool


class _RowActionContext(TypedDict):
    """A custom (non-built-in) action surfaced as a row button."""

    name: str
    label: str
    component: str  # the action page component name
    path: str  # ``/<key>/$id/<name>`` to navigate to
    when: str | None  # JS expression gating the row button


class _FilterContext(TypedDict):
    """Pre-rendered filter metadata for the list template."""

    id: str
    field: str
    label: str
    type: str
    op: str
    options: list[str]


@operation("resource_list", scope="project")
class ResourceList:
    """Emit a list page for every configured resource."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield list.tsx files."""
        config = ctx.instance

        for key, resource in config.resources.items():
            if resource.list_fn is None:
                # Without a list endpoint there's no list page to
                # render; the user wants a get/forms-only resource.
                continue

            pascal = _pascal(key)
            columns = _columns_for(resource.list.columns)
            row_actions = _row_actions_for(resource.actions, key, pascal)
            filters = _filters_for(resource.list.filters)
            has_create_toolbar = (
                "create" in resource.list.toolbar_actions
                and resource.create_fn is not None
                and resource.create is not None
            )
            has_delete_row = (
                "delete" in resource.list.row_actions
                and resource.delete_fn is not None
            )
            # Row-click drill-down requires both the list-side
            # opt-in AND a real detail surface to render.
            row_click_detail = (
                resource.list.row_click == "detail"
                and resource.detail is not None
                and resource.get_fn is not None
            )

            # Build the SDK import set lazily -- only import what
            # actually gets called below.
            sdk_imports: list[str] = [resource.list_fn]

            if resource.delete_fn is not None and has_delete_row:
                sdk_imports.append(resource.delete_fn)

            yield StaticFile(
                path=f"src/{key}/{pascal}List.tsx",
                template="src/resource/List.tsx.j2",
                context={
                    "key": key,
                    "pascal": pascal,
                    "label_singular": resource.label.singular,
                    "label_plural": resource.label.plural,
                    "list_item_type": resource.list_item_type,
                    "list_fn": resource.list_fn,
                    "delete_fn": (
                        resource.delete_fn if has_delete_row else None
                    ),
                    "columns": columns,
                    "has_create_toolbar": has_create_toolbar,
                    "has_delete_row": has_delete_row,
                    "row_actions": row_actions,
                    "filters": filters,
                    "page_size": resource.list.page_size,
                    "has_sortable": any(c["sortable"] for c in columns),
                    "row_click_detail": row_click_detail,
                    "list_path": f"/{key}",
                    "create_path": (
                        f"/{key}/new" if has_create_toolbar else None
                    ),
                    "detail_path": (
                        f"/{key}/$id" if row_click_detail else None
                    ),
                    "sdk_imports": sorted(sdk_imports),
                },
            )


def _columns_for(columns: list[ColumnSpec]) -> list[_ColumnContext]:
    """Materialize column specs into template-friendly dicts."""
    materialized: list[_ColumnContext] = []

    for i, col in enumerate(columns):
        materialized.append(
            {
                "field": col.field,
                "label": col.label or _humanize(col.field),
                "display": col.display,
                "is_first": i == 0,
                "sortable": col.sortable,
            },
        )

    return materialized


def _row_actions_for(
    actions: dict[str, ActionConfig],
    key: str,
    pascal: str,
) -> list[_RowActionContext]:
    """Filter actions for those that surface as row buttons."""
    out: list[_RowActionContext] = []

    for name, action in actions.items():
        if not action.row_action:
            continue

        out.append(
            {
                "name": name,
                "label": action.label,
                "component": f"{pascal}{_pascal(name)}Action",
                "path": f"/{key}/$id/{name}",
                "when": action.row_action_when,
            },
        )

    return out


def _humanize(field: str) -> str:
    """Title-case a snake_case field name for the column header."""
    return " ".join(p[:1].upper() + p[1:] for p in field.split("_"))


def _filters_for(filters: list[FilterSpec]) -> list[_FilterContext]:
    """Materialize filter specs with defaulted op + label."""
    return [
        {
            "id": f.field,
            "field": f.field,
            "label": f.label or _humanize(f.field),
            "type": f.type,
            "op": f.op or _DEFAULT_OP[f.type],
            "options": list(f.options),
        }
        for f in filters
    ]
