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
        assert 'Column id="name"' in out

    def test_first_column_marked_row_header(self) -> None:
        out = self._out(
            [ColumnSpec(field="name"), ColumnSpec(field="slug")],
        )

        # rowHeader is the announce-during-navigation hint glaze
        # uses; should always be on column 0.
        assert 'Column id="name" rowHeader' in out
        assert 'Column id="slug">' in out

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

        # queryKey: ["tasks", "list"]; invalidate on delete uses
        # just ["tasks"] so any list/get/sublist query gets nuked.
        assert 'queryKey: ["tasks", "list"]' in out
        assert 'invalidateQueries({ queryKey: ["tasks"] })' in out
