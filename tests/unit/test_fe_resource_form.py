"""Tests for :class:`fe.operations.resource_form.ResourceForm`.

Form files are emitted per resource that declares a ``create``
or ``update`` section *and* the matching SDK fn.  Files are
shaped so the list page's ``DrawerTrigger`` can wrap them.
"""

from __future__ import annotations

from fe.config import (
    FormConfig,
    ProjectConfig,
    ResourceConfig,
    ResourceLabel,
)
from fe.target import target as fe_target
from foundry.pipeline import generate


def _files(cfg: ProjectConfig) -> dict[str, str]:
    return {f.path: f.content for f in generate(cfg, fe_target)}


def _resource(  # noqa: PLR0913
    *,
    create_fn: str | None = None,
    create_fields: list[str] | None = None,
    update_fn: str | None = None,
    update_fields: list[str] | None = None,
    create_request_type: str | None = None,
    update_request_type: str | None = None,
) -> ResourceConfig:
    return ResourceConfig(
        label=ResourceLabel(singular="Project", plural="Projects"),
        list_item_type="ProjectListItem",
        **({"create_fn": create_fn} if create_fn else {}),
        **({"update_fn": update_fn} if update_fn else {}),
        **(
            {"create_request_type": create_request_type}
            if create_request_type
            else {}
        ),
        **(
            {"update_request_type": update_request_type}
            if update_request_type
            else {}
        ),
        create=(
            FormConfig(fields=create_fields or [])
            if create_fields is not None
            else None
        ),
        update=(
            FormConfig(fields=update_fields or [])
            if update_fields is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Conditional emission
# ---------------------------------------------------------------------------


class TestEmission:
    def test_no_create_section_no_create_form(self) -> None:
        out = _files(
            ProjectConfig(
                resources={
                    "projects": _resource(
                        create_fn="createFn",
                        # no create section -> no form
                    ),
                },
            ),
        )
        assert "src/projects/CreateProjectsForm.tsx" not in out

    def test_no_create_fn_no_create_form(self) -> None:
        out = _files(
            ProjectConfig(
                resources={
                    "projects": _resource(create_fields=["name"]),
                },
            ),
        )
        assert "src/projects/CreateProjectsForm.tsx" not in out

    def test_create_section_with_fn_emits_form(self) -> None:
        out = _files(
            ProjectConfig(
                resources={
                    "projects": _resource(
                        create_fn="createProjectV1TrackerProjectsPost",
                        create_fields=["name", "slug", "description"],
                    ),
                },
            ),
        )
        assert "src/projects/CreateProjectsForm.tsx" in out

    def test_update_section_with_fn_emits_form(self) -> None:
        out = _files(
            ProjectConfig(
                resources={
                    "projects": _resource(
                        update_fn="updateProjectV1TrackerProjectsIdPatch",
                        update_fields=["name"],
                    ),
                },
            ),
        )
        assert "src/projects/UpdateProjectsForm.tsx" in out


# ---------------------------------------------------------------------------
# Create form content
# ---------------------------------------------------------------------------


class TestCreateForm:
    def _out(self) -> str:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(
                    create_fn="createProjectV1TrackerProjectsPost",
                    create_fields=["name", "slug", "description"],
                    create_request_type="ProjectCreateRequest",
                ),
            },
        )
        return _files(cfg)["src/projects/CreateProjectsForm.tsx"]

    def test_imports_glaze_form_pieces_and_sdk_fn(self) -> None:
        out = self._out()

        assert "TextField" in out
        assert "DrawerBody" in out
        assert "DrawerFooter" in out
        assert "createProjectV1TrackerProjectsPost" in out
        assert 'from "../_generated/sdk.gen"' in out

    def test_imports_request_type_when_set(self) -> None:
        out = self._out()

        assert (
            'import type { ProjectCreateRequest } from "../_generated/'
            "types.gen" in out
        )
        assert "as ProjectCreateRequest" in out

    def test_one_text_field_per_form_field(self) -> None:
        out = self._out()

        for field in ("name", "slug", "description"):
            assert f'label="{field.title()}"' in out

    def test_field_state_uses_use_state(self) -> None:
        out = self._out()

        assert "const [name, setName] =" in out
        assert 'useState("")' in out

    def test_mutation_invalidates_resource_key(self) -> None:
        out = self._out()

        assert 'invalidateQueries({ queryKey: ["projects"] })' in out

    def test_success_toast_and_close(self) -> None:
        out = self._out()

        assert 'toast.success("Project created.")' in out
        assert "close()" in out

    def test_signature_takes_only_close_for_create(self) -> None:
        out = self._out()

        assert "export function CreateProjectsForm({ close }: Props)" in out


# ---------------------------------------------------------------------------
# Update form content
# ---------------------------------------------------------------------------


class TestUpdateForm:
    def _out(self) -> str:
        cfg = ProjectConfig(
            resources={
                "projects": _resource(
                    update_fn="updateProjectV1TrackerProjectsIdPatch",
                    update_fields=["name", "description"],
                ),
            },
        )
        return _files(cfg)["src/projects/UpdateProjectsForm.tsx"]

    def test_signature_takes_close_and_id(self) -> None:
        out = self._out()

        assert "export function UpdateProjectsForm({ close, id }: Props)" in out

    def test_mutation_passes_path_id(self) -> None:
        out = self._out()

        assert "path: { id }" in out

    def test_success_toast_uses_updated_verb(self) -> None:
        out = self._out()

        assert "Project updated." in out
