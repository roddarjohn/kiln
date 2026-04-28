"""Tests for :class:`fe.operations.resource_list.ResourceList`.

The list op emits one ``{Pascal}List.tsx`` per resource that
declares a ``list_fn``.  The page composes glaze's Table with
the openapi-ts-generated React-Query hook + a configurable mix
of toolbar / row actions.
"""

from __future__ import annotations

from fe.config import (
    ActionConfig,
    ColumnSpec,
    DetailConfig,
    DetailSection,
    FilterSpec,
    FormConfig,
    ListConfig,
    ProjectConfig,
    ResourceConfig,
    ResourceLabel,
)
from fe.target import target as fe_target
from foundry.pipeline import generate


def _files(cfg: ProjectConfig) -> dict[str, str]:
    return {f.path: f.content for f in generate(cfg, fe_target)}


# ---------------------------------------------------------------------------
# Conditional emission
# ---------------------------------------------------------------------------


class TestEmission:
    def test_no_resources_no_list_files(self) -> None:
        out = _files(ProjectConfig())

        assert not any(p.endswith("List.tsx") for p in out)

    def test_resource_without_list_fn_skipped(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                ),
            },
        )
        out = _files(cfg)

        assert "src/projects/ProjectsList.tsx" not in out

    def test_resource_with_list_fn_emits_list_page(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                    list_fn="listProjectsV1TrackerProjectsSearchPost",
                    list=ListConfig(columns=[ColumnSpec(field="name")]),
                ),
            },
        )
        out = _files(cfg)

        assert "src/projects/ProjectsList.tsx" in out
        page = out["src/projects/ProjectsList.tsx"]
        assert "export function ProjectsList()" in page
        assert "listProjectsV1TrackerProjectsSearchPost" in page


# ---------------------------------------------------------------------------
# Page content
# ---------------------------------------------------------------------------


class TestColumns:
    def _out(self, columns: list[ColumnSpec]) -> str:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                    list_fn="listProjectsV1TrackerProjectsSearchPost",
                    list=ListConfig(columns=columns),
                ),
            },
        )
        return _files(cfg)["src/projects/ProjectsList.tsx"]

    def test_text_column_renders_value(self) -> None:
        out = self._out([ColumnSpec(field="name")])

        assert '{String(item.name ?? "")}' in out
        assert 'id="name"' in out

    def test_first_column_marked_row_header(self) -> None:
        out = self._out(
            [ColumnSpec(field="name"), ColumnSpec(field="slug")],
        )

        # rowHeader is the announce-during-navigation hint glaze
        # uses; should always be on column 0.  The Column tag
        # spans multiple lines in the generated output, so we
        # check id + the rowHeader marker independently.
        assert 'id="name"' in out
        assert 'id="slug"' in out
        # The rowHeader attribute appears once -- on the first
        # column -- and only on the first column.
        assert out.count("rowHeader") == 1

    def test_badge_column_imports_badge(self) -> None:
        out = self._out(
            [
                ColumnSpec(field="title"),
                ColumnSpec(field="completed", display="badge"),
            ],
        )

        assert "Badge" in out
        assert 'tone={item.completed ? "success" : "neutral"}' in out

    def test_text_only_columns_skip_badge_import(self) -> None:
        out = self._out([ColumnSpec(field="name"), ColumnSpec(field="slug")])

        assert "Badge" not in out

    def test_humanized_default_label(self) -> None:
        out = self._out([ColumnSpec(field="created_at")])

        assert "Created At" in out

    def test_explicit_label_overrides_default(self) -> None:
        out = self._out([ColumnSpec(field="created_at", label="When")])

        assert "When" in out
        assert "Created At" not in out


# ---------------------------------------------------------------------------
# Toolbar / row actions
# ---------------------------------------------------------------------------


class TestActions:
    def _resource(
        self,
        *,
        delete_fn: str | None = None,
        create_fn: str | None = None,
        toolbar: list[str] | None = None,
        row_actions: list[str] | None = None,
        actions: dict[str, ActionConfig] | None = None,
    ) -> ResourceConfig:
        return ResourceConfig(
            label=ResourceLabel(singular="Project", plural="Projects"),
            list_item_type="ProjectListItem",
            list_fn="listProjectsV1TrackerProjectsSearchPost",
            **({"delete_fn": delete_fn} if delete_fn else {}),
            **({"create_fn": create_fn} if create_fn else {}),
            create=FormConfig(fields=["name"]) if create_fn else None,
            list=ListConfig(
                columns=[ColumnSpec(field="name")],
                toolbar_actions=toolbar or [],  # type: ignore[arg-type]
                row_actions=row_actions or [],  # type: ignore[arg-type]
            ),
            actions=actions or {},
        )

    def test_create_toolbar_renders_button_and_drawer(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": self._resource(
                    create_fn="createProjectV1TrackerProjectsPost",
                    toolbar=["create"],
                ),
            },
        )
        out = _files(cfg)["src/projects/ProjectsList.tsx"]

        assert "DrawerTrigger" in out
        assert "<Drawer" in out
        assert "Create" in out
        # The form component that the drawer renders is owned by
        # the form op; the list page just imports it by name.
        assert "<CreateProjectsForm" in out
        assert (
            'import { CreateProjectsForm } from "./CreateProjectsForm";' in out
        )

    def test_create_toolbar_skipped_without_create_fn(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": self._resource(toolbar=["create"]),
            },
        )
        out = _files(cfg)["src/projects/ProjectsList.tsx"]

        assert "DrawerTrigger" not in out
        assert "CreateProjectsForm" not in out

    def test_delete_row_renders_button_and_mutation(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": self._resource(
                    delete_fn="deleteProjectV1TrackerProjectsIdDelete",
                    row_actions=["delete"],
                ),
            },
        )
        out = _files(cfg)["src/projects/ProjectsList.tsx"]

        assert "deleteProjectV1TrackerProjectsIdDelete" in out
        assert "useMutation" in out
        assert 'variant="danger"' in out
        assert "Project deleted." in out

    def test_custom_row_action_renders_dialog_trigger(self) -> None:
        cfg = ProjectConfig(
            resources={
                "tasks": self._resource(
                    actions={
                        "complete": ActionConfig(
                            label="Complete",
                            fn="completeFn",
                            row_action=True,
                            row_action_when="!item.completed",
                        ),
                    },
                ),
            },
        )
        out = _files(cfg)["src/tasks/TasksList.tsx"]

        assert "DialogTrigger" in out
        assert "TasksCompleteAction" in out
        assert "!item.completed" in out
        # Conditional row action uses a ternary inside the Cell.
        assert "? (" in out
        assert ") : null" in out

    def test_actions_imports_each_action_component(self) -> None:
        cfg = ProjectConfig(
            resources={
                "tasks": self._resource(
                    actions={
                        "complete": ActionConfig(
                            label="Complete",
                            fn="completeFn",
                            row_action=True,
                        ),
                    },
                ),
            },
        )
        out = _files(cfg)["src/tasks/TasksList.tsx"]

        assert (
            'import { TasksCompleteAction } from "./actions/'
            "TasksCompleteAction" in out
        )


# ---------------------------------------------------------------------------
# Row click -> detail drawer
# ---------------------------------------------------------------------------


class TestRowClickDetail:
    def _resource(
        self,
        *,
        get_fn: str | None = None,
        detail: DetailConfig | None = None,
        row_click: str | None = None,
    ) -> ResourceConfig:
        return ResourceConfig(
            label=ResourceLabel(singular="Project", plural="Projects"),
            list_item_type="ProjectListItem",
            list_fn="listProjectsV1TrackerProjectsSearchPost",
            **({"get_fn": get_fn} if get_fn else {}),
            detail=detail,
            list=ListConfig(
                columns=[ColumnSpec(field="name")],
                row_click=row_click,  # type: ignore[arg-type]
            ),
        )

    def test_row_click_none_skips_detail_drawer(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": self._resource(
                    get_fn="getFn",
                    detail=DetailConfig(
                        sections=[DetailSection(fields=["name"])],
                    ),
                ),
            },
        )
        out = _files(cfg)["src/projects/ProjectsList.tsx"]

        assert "ProjectsDetail" not in out
        assert "useState" not in out

    def test_row_click_detail_without_detail_config_skipped(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": self._resource(
                    get_fn="getFn",
                    row_click="detail",
                ),
            },
        )
        out = _files(cfg)["src/projects/ProjectsList.tsx"]

        assert "ProjectsDetail" not in out
        assert "onRowAction" not in out

    def test_row_click_detail_wires_drawer_and_state(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": self._resource(
                    get_fn="getProjectV1TrackerProjectsIdGet",
                    detail=DetailConfig(
                        sections=[DetailSection(fields=["name"])],
                    ),
                    row_click="detail",
                ),
            },
        )
        out = _files(cfg)["src/projects/ProjectsList.tsx"]

        assert 'import { ProjectsDetail } from "./ProjectsDetail"' in out
        assert 'import { useState } from "react"' in out
        assert "const [openId, setOpenId] = useState" in out
        assert "onRowAction" in out
        assert "<Drawer" in out
        assert "<ProjectsDetail" in out


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    def _out(self, filters: list[FilterSpec]) -> str:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                    list_fn="listProjectsV1TrackerProjectsSearchPost",
                    list=ListConfig(
                        columns=[ColumnSpec(field="name")],
                        filters=filters,
                    ),
                ),
            },
        )
        return _files(cfg)["src/projects/ProjectsList.tsx"]

    def test_no_filters_omits_filterbar(self) -> None:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                    list_fn="listProjectsV1TrackerProjectsSearchPost",
                    list=ListConfig(columns=[ColumnSpec(field="name")]),
                ),
            },
        )
        out = _files(cfg)["src/projects/ProjectsList.tsx"]

        assert "FilterBar" not in out
        assert "useFilters" not in out
        assert "filter:" not in out  # No filter key in body

    def test_text_filter_renders_textfield(self) -> None:
        out = self._out([FilterSpec(field="name", type="text")])

        assert "FilterBar" in out
        assert "useFilters" in out
        assert "TextField" in out
        # Default op for text is "contains"
        assert '"contains"' in out
        # ID + label
        assert '"name"' in out
        assert "Name" in out

    def test_boolean_filter_renders_switch(self) -> None:
        out = self._out(
            [FilterSpec(field="completed", type="boolean")],
        )

        assert "Switch" in out
        # Default op for boolean is "eq"
        assert '"eq"' in out
        assert "v === true" in out

    def test_select_filter_renders_select_with_options(self) -> None:
        out = self._out(
            [
                FilterSpec(
                    field="status",
                    type="select",
                    options=["draft", "published"],
                ),
            ],
        )

        assert "Select" in out
        assert "SelectItem" in out
        assert "draft" in out
        assert "published" in out

    def test_explicit_op_overrides_default(self) -> None:
        out = self._out([FilterSpec(field="name", type="text", op="eq")])

        # Single op-quoted string, not "contains"
        assert '"eq"' in out
        assert '"contains"' not in out

    def test_filter_label_defaults_to_humanized_field(self) -> None:
        out = self._out([FilterSpec(field="created_at", type="text")])
        assert "Created At" in out

    def test_explicit_label_overrides_default(self) -> None:
        out = self._out(
            [FilterSpec(field="created_at", type="text", label="When")],
        )
        assert "When" in out
        assert "Created At" not in out

    def test_query_body_includes_filter_builder(self) -> None:
        out = self._out([FilterSpec(field="name", type="text")])

        # The body now wires the build helper.
        assert "buildProjectsFilter" in out
        assert "filterState.values" in out
        # The query key includes the filter values so the query
        # refetches on filter changes.
        assert "filterState.values," in out

    def test_multiple_active_conditions_wrapped_in_and(self) -> None:
        out = self._out(
            [
                FilterSpec(field="name", type="text"),
                FilterSpec(field="completed", type="boolean"),
            ],
        )

        # Helper falls back to `{ and: conds }` when there are
        # multiple active conditions.
        assert "{ and: conds }" in out


# ---------------------------------------------------------------------------
# Query keys
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pagination + sort
# ---------------------------------------------------------------------------


class TestPagination:
    def _out(self, page_size: int | None) -> str:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                    list_fn="listProjectsV1TrackerProjectsSearchPost",
                    list=ListConfig(
                        columns=[ColumnSpec(field="name")],
                        **({"page_size": page_size} if page_size else {}),
                    ),
                ),
            },
        )
        return _files(cfg)["src/projects/ProjectsList.tsx"]

    def test_no_page_size_omits_pagination(self) -> None:
        out = self._out(None)

        assert "Pagination" not in out
        assert "PAGE_SIZE" not in out
        assert "offset:" not in out
        assert "limit:" not in out

    def test_page_size_wires_pagination(self) -> None:
        out = self._out(20)

        # Pagination component + page state.
        assert "Pagination" in out
        assert "const [page, setPage] = useState(1)" in out
        assert "PAGE_SIZE = 20" in out
        # Body sends offset + limit.
        assert "offset: (page - 1) * PAGE_SIZE" in out
        assert "limit: PAGE_SIZE" in out
        # Query key includes page so the data refetches.
        assert "page," in out

    def test_pagination_uses_total_pages_from_response(self) -> None:
        out = self._out(20)

        # Prefer BE-supplied total_pages; fall back only if
        # missing (older BEs).
        assert "total_pages" in out


class TestSorting:
    def _out(self, columns: list[ColumnSpec]) -> str:
        cfg = ProjectConfig(
            resources={
                "projects": ResourceConfig(
                    label=ResourceLabel(singular="Project", plural="Projects"),
                    list_item_type="ProjectListItem",
                    list_fn="listProjectsV1TrackerProjectsSearchPost",
                    list=ListConfig(columns=columns),
                ),
            },
        )
        return _files(cfg)["src/projects/ProjectsList.tsx"]

    def test_no_sortable_columns_skips_sort(self) -> None:
        out = self._out([ColumnSpec(field="name")])

        assert "sortDescriptor" not in out
        assert "onSortChange" not in out
        assert "sort:" not in out

    def test_sortable_column_wires_table_props(self) -> None:
        out = self._out(
            [
                ColumnSpec(field="name", sortable=True),
                ColumnSpec(field="slug"),
            ],
        )

        # Table receives sortDescriptor + onSortChange; sortable
        # column gets allowsSorting; non-sortable does not.
        assert "sortDescriptor={sortDescriptor}" in out
        assert "onSortChange={setSortDescriptor}" in out
        assert "allowsSorting" in out
        # The body sort is built from the descriptor.
        assert "sort: sortDescriptor" in out
        assert "field: String(sortDescriptor.column)" in out
        assert '"descending"' in out  # ascending/descending mapping
        # SortDescriptor type is imported -- glaze hasn't shipped
        # a re-export yet (#27).
        assert (
            'import type { SortDescriptor } from "react-aria-components"' in out
        )

    def test_sortable_only_applied_to_marked_columns(self) -> None:
        out = self._out(
            [
                ColumnSpec(field="name", sortable=True),
                ColumnSpec(field="slug", sortable=False),
            ],
        )

        # Exactly one allowsSorting -- the name column only.
        assert out.count("allowsSorting") == 1


# ---------------------------------------------------------------------------
# Query keys
# ---------------------------------------------------------------------------


class TestQueryKeys:
    def test_list_query_key_uses_resource_key(self) -> None:
        cfg = ProjectConfig(
            resources={
                "tasks": ResourceConfig(
                    label=ResourceLabel(singular="Task", plural="Tasks"),
                    list_item_type="TaskListItem",
                    list_fn="listTasksFn",
                    delete_fn="deleteTaskFn",
                    list=ListConfig(
                        columns=[ColumnSpec(field="title")],
                        row_actions=["delete"],
                    ),
                ),
            },
        )
        out = _files(cfg)["src/tasks/TasksList.tsx"]

        # queryKey starts ["tasks", "list", ...]; invalidate on
        # delete uses just ["tasks"] so any list/get/sublist query
        # gets nuked.
        assert '"tasks"' in out
        assert '"list"' in out
        assert 'invalidateQueries({ queryKey: ["tasks"] })' in out
