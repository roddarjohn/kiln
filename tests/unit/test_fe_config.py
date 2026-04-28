"""Tests for the expanded fe config schema + jsonnet stdlib.

The fe target's config grew from a flat 5-field model into a
hierarchical schema covering shell, auth, resources, list views,
forms, and actions.  These tests:

* Round-trip a representative jsonnet config through the
  loader -> Pydantic schema, asserting the parsed tree.
* Verify the jsonnet stdlib helpers (``fe.shell``, ``fe.nav``,
  ``fe.resource``, ``fe.presets.crud``) produce dicts the schema
  accepts.
* Confirm strict-extra is on so config typos surface at parse
  time rather than at codegen time.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from fe.config import (
    ActionConfig,
    AuthConfig,
    ColumnSpec,
    FormConfig,
    ListConfig,
    NavItem,
    ProjectConfig,
    ResourceConfig,
    ResourceLabel,
    ShellConfig,
)
from fe.target import target as fe_target
from foundry.config import load_config

if TYPE_CHECKING:
    from pathlib import Path


def _write(tmp_path: Path, content: str) -> Path:
    cfg = tmp_path / "fe.jsonnet"
    cfg.write_text(textwrap.dedent(content))
    return cfg


# ---------------------------------------------------------------------------
# Pydantic-only round trips (no jsonnet)
# ---------------------------------------------------------------------------


class TestProjectConfigDefaults:
    def test_minimum_config_uses_defaults(self) -> None:
        cfg = ProjectConfig.model_validate({})
        assert cfg.openapi_spec == "../be/openapi.json"
        assert cfg.output_dir == "src/_generated"
        assert cfg.client == "@hey-api/client-fetch"
        assert cfg.react_query is True
        assert cfg.format is None
        assert cfg.shell is None
        assert cfg.auth is None
        assert cfg.resources == {}

    def test_unknown_top_level_fields_reject(self) -> None:
        with pytest.raises(ValidationError):
            ProjectConfig.model_validate({"bogus": True})


class TestShellConfig:
    def test_defaults(self) -> None:
        s = ShellConfig.model_validate({})
        assert s.brand == "App"
        assert s.nav == []
        assert s.user_menu is True

    def test_nav_items(self) -> None:
        s = ShellConfig.model_validate(
            {
                "brand": "kiln",
                "nav": [
                    {"label": "Projects", "view": "projects"},
                    {"label": "Tasks", "view": "tasks"},
                ],
            },
        )
        assert s.brand == "kiln"
        assert s.nav == [
            NavItem(label="Projects", view="projects"),
            NavItem(label="Tasks", view="tasks"),
        ]

    def test_extra_keys_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ShellConfig.model_validate({"brnd": "typo"})


class TestAuthConfig:
    def test_required_fields_must_be_present(self) -> None:
        with pytest.raises(ValidationError):
            AuthConfig.model_validate({})

    def test_minimum_required(self) -> None:
        a = AuthConfig.model_validate(
            {
                "login_fn": "createTokenV1AuthTokenPost",
                "validate_fn": "readSessionV1AuthTokenGet",
                "logout_fn": "logoutV1AuthTokenLogoutPost",
            },
        )
        assert a.storage == "localStorage"
        assert a.token_key == "glaze:auth:token"  # noqa: S105
        assert a.session_type == "Session"
        assert a.credentials_type == "LoginCredentials"
        assert a.credentials_fields == ["username", "password"]

    def test_storage_choices(self) -> None:
        for backend in ("localStorage", "sessionStorage", "memory", "cookie"):
            AuthConfig.model_validate(
                {
                    "storage": backend,
                    "login_fn": "x",
                    "validate_fn": "y",
                    "logout_fn": "z",
                },
            )

        with pytest.raises(ValidationError):
            AuthConfig.model_validate(
                {
                    "storage": "redis",
                    "login_fn": "x",
                    "validate_fn": "y",
                    "logout_fn": "z",
                },
            )


class TestResourceConfig:
    def test_minimum_resource(self) -> None:
        r = ResourceConfig.model_validate(
            {
                "label": {"singular": "Project", "plural": "Projects"},
                "list_item_type": "ProjectListItem",
            },
        )
        assert r.label == ResourceLabel(singular="Project", plural="Projects")
        assert r.list == ListConfig()
        assert r.create is None
        assert r.update is None
        assert r.actions == {}

    def test_full_resource(self) -> None:
        r = ResourceConfig.model_validate(
            {
                "label": {"singular": "Task", "plural": "Tasks"},
                "list_item_type": "TaskListItem",
                "list_fn": "listTasksV1TrackerTasksSearchPost",
                "list": {
                    "columns": [
                        {"field": "title"},
                        {"field": "completed", "display": "badge"},
                    ],
                },
                "actions": {
                    "complete": {
                        "label": "Complete",
                        "fn": "completeActionV1TrackerTasksIdCompletePost",
                        "request_schema": "CompleteRequest",
                        "fields": ["note"],
                        "presentation": "modal",
                        "row_action": True,
                        "row_action_when": "!item.completed",
                    },
                },
            },
        )
        assert r.list.columns == [
            ColumnSpec(field="title"),
            ColumnSpec(field="completed", display="badge"),
        ]
        assert "complete" in r.actions
        complete = r.actions["complete"]
        assert isinstance(complete, ActionConfig)
        assert complete.label == "Complete"
        assert complete.row_action is True
        assert complete.row_action_when == "!item.completed"

    def test_form_presentation_choices(self) -> None:
        FormConfig.model_validate(
            {"fields": ["name"], "presentation": "drawer"},
        )
        FormConfig.model_validate(
            {"fields": ["name"], "presentation": "modal"},
        )
        FormConfig.model_validate(
            {"fields": ["name"], "presentation": "page"},
        )

        with pytest.raises(ValidationError):
            FormConfig.model_validate(
                {"fields": ["name"], "presentation": "tooltip"},
            )

    def test_unknown_resource_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResourceConfig.model_validate(
                {
                    "label": {"singular": "Project", "plural": "Projects"},
                    "list_item_type": "ProjectListItem",
                    "extra": "nope",
                },
            )


# ---------------------------------------------------------------------------
# Jsonnet round-trips
# ---------------------------------------------------------------------------


def _load(path: Path) -> ProjectConfig:
    """Load a fe.jsonnet using the fe target's stdlib directory."""
    stdlib = fe_target.jsonnet_stdlib_dir
    assert stdlib is not None, "fe target must declare a jsonnet stdlib"
    parsed = load_config(path, ProjectConfig, stdlibs={"fe": stdlib})
    assert isinstance(parsed, ProjectConfig)
    return parsed


class TestJsonnetStdlib:
    def test_minimal_jsonnet_round_trip(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path,
            """
            local fe = import "fe/main.libsonnet";
            {
              openapi_spec: "../be/openapi.json",
            }
            """,
        )
        parsed = _load(cfg)
        assert parsed.openapi_spec == "../be/openapi.json"
        assert parsed.shell is None

    def test_shell_helper_round_trip(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path,
            """
            local fe = import "fe/main.libsonnet";
            {
              shell: fe.shell({
                brand: "kiln-sample",
                nav: [
                  fe.nav.item("Projects", "projects"),
                  fe.nav.item("Tasks",    "tasks"),
                ],
              }),
            }
            """,
        )
        parsed = _load(cfg)
        assert parsed.shell is not None
        assert parsed.shell.brand == "kiln-sample"
        assert parsed.shell.nav == [
            NavItem(label="Projects", view="projects"),
            NavItem(label="Tasks", view="tasks"),
        ]

    def test_auth_helper_round_trip(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path,
            """
            local fe = import "fe/main.libsonnet";
            {
              auth: fe.auth({
                login_fn:    "createTokenV1AuthTokenPost",
                validate_fn: "readSessionV1AuthTokenGet",
                logout_fn:   "logoutV1AuthTokenLogoutPost",
                login_hint:  "Try alice / wonderland",
              }),
            }
            """,
        )
        parsed = _load(cfg)
        assert parsed.auth is not None
        assert parsed.auth.login_fn == "createTokenV1AuthTokenPost"
        assert parsed.auth.login_hint == "Try alice / wonderland"
        assert parsed.auth.storage == "localStorage"

    def test_crud_preset_round_trip(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path,
            """
            local fe = import "fe/main.libsonnet";
            {
              resources: {
                project: fe.presets.crud({
                  label_singular: "Project",
                  label_plural:   "Projects",
                  list_item_type: "ProjectListItem",
                  list_fn:   "listProjectsV1TrackerProjectsSearchPost",
                  create_fn: "createProjectV1TrackerProjectsPost",
                  delete_fn: "deleteProjectV1TrackerProjectsIdDelete",
                  create_request_type: "ProjectCreateRequest",
                  columns: ["name", "slug"],
                  create_fields: ["name", "slug", "description"],
                }),
              },
            }
            """,
        )
        parsed = _load(cfg)
        assert "project" in parsed.resources
        proj = parsed.resources["project"]
        assert proj.label.plural == "Projects"
        assert proj.list_fn == "listProjectsV1TrackerProjectsSearchPost"
        assert proj.list.columns == [
            ColumnSpec(field="name"),
            ColumnSpec(field="slug"),
        ]
        # Preset auto-includes create + delete based on fn presence.
        assert proj.list.toolbar_actions == ["create"]
        assert proj.list.row_actions == ["delete"]
        assert proj.create is not None
        assert proj.create.fields == ["name", "slug", "description"]
        assert proj.create.presentation == "drawer"

    def test_resource_with_action_round_trip(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path,
            """
            local fe = import "fe/main.libsonnet";
            {
              resources: {
                task: fe.resource({
                  label: fe.label("Task", "Tasks"),
                  list_item_type: "TaskListItem",
                  list_fn: "listTasksV1TrackerTasksSearchPost",
                  list: fe.list({
                    columns: [
                      fe.column("title"),
                      fe.column("completed", display="badge"),
                    ],
                  }),
                  actions: {
                    complete: fe.action({
                      label: "Complete",
                      fn:    "completeActionV1TrackerTasksIdCompletePost",
                      request_schema: "CompleteRequest",
                      fields: ["note"],
                      presentation: "modal",
                      row_action: true,
                      row_action_when: "!item.completed",
                    }),
                  },
                }),
              },
            }
            """,
        )
        parsed = _load(cfg)
        task = parsed.resources["task"]
        assert task.list.columns == [
            ColumnSpec(field="title"),
            ColumnSpec(field="completed", display="badge"),
        ]
        complete = task.actions["complete"]
        assert complete.row_action is True
        assert complete.row_action_when == "!item.completed"
