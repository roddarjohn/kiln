"""Tests for :class:`fe.operations.resource_detail.ResourceDetail`."""

from __future__ import annotations

from fe.config import (
    ActionConfig,
    DetailConfig,
    DetailSection,
    ProjectConfig,
    ResourceConfig,
    ResourceLabel,
)
from fe.target import target as fe_target
from foundry.pipeline import generate


def _files(cfg: ProjectConfig) -> dict[str, str]:
    return {f.path: f.content for f in generate(cfg, fe_target)}


def _cfg(
    *,
    detail: DetailConfig | None = None,
    get_fn: str | None = "getProjectV1TrackerProjectsIdGet",
    actions: dict[str, ActionConfig] | None = None,
) -> ProjectConfig:
    return ProjectConfig(
        resources={
            "projects": ResourceConfig(
                label=ResourceLabel(singular="Project", plural="Projects"),
                list_item_type="ProjectListItem",
                resource_type="ProjectResource",
                **({"get_fn": get_fn} if get_fn else {}),
                detail=detail,
                actions=actions or {},
            ),
        },
    )


# ---------------------------------------------------------------------------
# Conditional emission
# ---------------------------------------------------------------------------


class TestEmission:
    def test_no_detail_no_file(self) -> None:
        out = _files(_cfg(detail=None))
        assert "src/projects/ProjectsDetail.tsx" not in out

    def test_no_get_fn_no_file(self) -> None:
        out = _files(
            _cfg(
                get_fn=None,
                detail=DetailConfig(
                    sections=[DetailSection(fields=["name"])],
                ),
            ),
        )
        assert "src/projects/ProjectsDetail.tsx" not in out

    def test_emits_detail_when_configured(self) -> None:
        out = _files(
            _cfg(
                detail=DetailConfig(
                    sections=[DetailSection(fields=["name", "slug"])],
                ),
            ),
        )
        assert "src/projects/ProjectsDetail.tsx" in out


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


class TestSections:
    def _out(self, sections: list[DetailSection]) -> str:
        return _files(_cfg(detail=DetailConfig(sections=sections)))[
            "src/projects/ProjectsDetail.tsx"
        ]

    def test_fetches_via_get_fn(self) -> None:
        out = self._out([DetailSection(fields=["name"])])

        assert "getProjectV1TrackerProjectsIdGet" in out
        assert "useQuery" in out
        assert 'queryKey: ["projects", "get", id]' in out

    def test_fields_render_label_value_pairs(self) -> None:
        out = self._out(
            [DetailSection(title="Overview", fields=["name", "slug"])],
        )

        assert "Overview" in out
        # Section fields render through glaze's DataList primitive
        # (#30), one row per declared field.
        assert "<DataList" in out
        assert '"Name"' in out
        assert '"Slug"' in out
        # DataList accepts ReactNode and stringifies primitives
        # itself (#53), so values pass through raw.
        assert "value: item.name," in out
        assert "value: item.slug," in out

    def test_titleless_section_omits_heading(self) -> None:
        out = self._out([DetailSection(fields=["name"])])

        # No <Heading> emitted when the section has no title.
        assert "Heading" not in out or "level={1}" not in out

    def test_component_section_imports_and_renders_custom(self) -> None:
        out = self._out(
            [DetailSection(title="Tasks", component="../widgets/TasksTab")],
        )

        # Custom component import + render with `item` prop.
        assert 'import { Projects1Section } from "../widgets/TasksTab"' in out
        assert "<Projects1Section item={item} />" in out

    def test_uses_resource_type_via_query_data(self) -> None:
        out = self._out([DetailSection(fields=["name"])])

        # The resource type isn't imported by name -- the React-Query
        # hook infers it from get_fn -- but the get_fn import is.
        assert "getProjectV1TrackerProjectsIdGet" in out
        assert 'from "../_generated/sdk.gen"' in out


# ---------------------------------------------------------------------------
# Header actions
# ---------------------------------------------------------------------------


class TestHeaderActions:
    def test_action_referenced_by_name_renders_button(self) -> None:
        cfg = _cfg(
            detail=DetailConfig(
                sections=[DetailSection(fields=["name"])],
                actions=["publish"],
            ),
            actions={
                "publish": ActionConfig(label="Publish", fn="publishFn"),
            },
        )
        out = _files(cfg)["src/projects/ProjectsDetail.tsx"]

        # Detail header action buttons navigate to the action's
        # own route (``/<key>/$id/<name>``) rather than opening a
        # dialog -- actions are fully addressable surfaces now.
        assert "/projects/$id/publish" in out
        assert "Publish" in out

    def test_unknown_action_name_silently_skipped(self) -> None:
        cfg = _cfg(
            detail=DetailConfig(
                sections=[DetailSection(fields=["name"])],
                actions=["nope"],
            ),
        )
        out = _files(cfg)["src/projects/ProjectsDetail.tsx"]

        # No action component dragged in.
        assert "Action" not in out
