"""Tests for :class:`fe.operations.resource_action.ResourceAction`."""

from __future__ import annotations

from fe.config import (
    ActionConfig,
    ProjectConfig,
    ResourceConfig,
    ResourceLabel,
)
from fe.target import target as fe_target
from foundry.pipeline import generate


def _files(cfg: ProjectConfig) -> dict[str, str]:
    return {f.path: f.content for f in generate(cfg, fe_target)}


def _cfg(actions: dict[str, ActionConfig]) -> ProjectConfig:
    return ProjectConfig(
        resources={
            "tasks": ResourceConfig(
                label=ResourceLabel(singular="Task", plural="Tasks"),
                list_item_type="TaskListItem",
                actions=actions,
            ),
        },
    )


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


class TestEmission:
    def test_no_actions_no_files(self) -> None:
        out = _files(_cfg({}))

        assert not any("/actions/" in p for p in out)

    def test_each_action_yields_one_file(self) -> None:
        out = _files(
            _cfg(
                {
                    "complete": ActionConfig(label="Complete", fn="completeFn"),
                    "archive": ActionConfig(label="Archive", fn="archiveFn"),
                },
            ),
        )

        assert "src/tasks/actions/TasksCompleteAction.tsx" in out
        assert "src/tasks/actions/TasksArchiveAction.tsx" in out


# ---------------------------------------------------------------------------
# Component content
# ---------------------------------------------------------------------------


class TestActionContent:
    def _out(
        self,
        *,
        fields: list[str] | None = None,
        confirm_text: str | None = None,
        request_schema: str | None = None,
    ) -> str:
        cfg = _cfg(
            {
                "complete": ActionConfig(
                    label="Complete",
                    fn="completeActionV1TrackerTasksIdCompletePost",
                    fields=fields or [],
                    **(
                        {"confirm_text": confirm_text}
                        if confirm_text is not None
                        else {}
                    ),
                    **(
                        {"request_schema": request_schema}
                        if request_schema is not None
                        else {}
                    ),
                ),
            },
        )
        return _files(cfg)["src/tasks/actions/TasksCompleteAction.tsx"]

    def test_imports_sdk_fn(self) -> None:
        out = self._out()
        assert "completeActionV1TrackerTasksIdCompletePost" in out

    def test_imports_list_item_type_for_props(self) -> None:
        out = self._out()
        assert (
            'import type { TaskListItem } from "../../_generated/types.gen"'
            in out
        )

    def test_path_uses_item_id(self) -> None:
        out = self._out()
        assert "path: { id: String(item.id) }" in out

    def test_no_fields_skips_textfield(self) -> None:
        out = self._out()
        assert "TextField" not in out

    def test_field_renders_textfield(self) -> None:
        out = self._out(fields=["note"])

        assert "TextField" in out
        assert 'label="Note"' in out
        assert "const [note, setNote] =" in out

    def test_request_schema_cast_on_body(self) -> None:
        out = self._out(fields=["note"], request_schema="CompleteRequest")

        assert (
            'import type { CompleteRequest } from "../../_generated/types.gen"'
            in out
        )
        assert "as CompleteRequest" in out

    def test_confirm_text_rendered_above_form(self) -> None:
        out = self._out(confirm_text="Mark as done?")
        assert "Mark as done?" in out
        # And only when set:
        plain = self._out()
        assert "Mark as done?" not in plain

    def test_invalidates_resource_key_on_success(self) -> None:
        out = self._out()
        assert 'invalidateQueries({ queryKey: ["tasks"] })' in out
