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
    get_fn: str | None = None,
) -> ResourceConfig:
    # Update forms need ``get_fn`` to pre-populate fields.  Default
    # it on so update tests don't have to wire it explicitly.
    if update_fn is not None and get_fn is None:
        get_fn = "getFn"

    return ResourceConfig(
        label=ResourceLabel(singular="Project", plural="Projects"),
        list_item_type="ProjectListItem",
        **({"create_fn": create_fn} if create_fn else {}),
        **({"update_fn": update_fn} if update_fn else {}),
        **({"get_fn": get_fn} if get_fn else {}),
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

        # Forms are now full-page route components: PageHeader on
        # top, Card body for the inputs, no Drawer wrapping.
        assert "TextField" in out
        assert "PageHeader" in out
        assert "Card" in out
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

    def test_form_uses_useFormMutation_and_native_form(self) -> None:  # noqa: N802
        out = self._out()

        # Forms now ride glaze's useFormMutation + native <Form>
        # (#32) -- no per-field useState boilerplate, the FormData
        # is collected from named TextFields at submit time.
        assert "useFormMutation" in out
        assert "<Form" in out
        assert "onSubmit={onSubmit}" in out
        assert "validationErrors={validationErrors}" in out
        assert "useState" not in out
        # Each field is uncontrolled with a `name` for FormData.
        assert 'name="name"' in out

    def test_mutation_invalidates_resource_key(self) -> None:
        out = self._out()

        assert 'invalidateQueries({ queryKey: ["projects"] })' in out

    def test_success_toast_and_navigates_back(self) -> None:
        out = self._out()

        # On success the form toasts and navigates back to the
        # list (or the detail for update); ``close`` props are
        # gone with the drawer wrapper.
        assert 'toast.success("Project created.")' in out
        assert "back()" in out

    def test_signature_takes_no_props(self) -> None:
        out = self._out()

        # The form is a route component, no parent passes props.
        assert "export function CreateProjectsForm()" in out


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

    def test_signature_takes_no_props_and_reads_id_from_route(self) -> None:
        out = self._out()

        # Update form is also a route component now; ``id`` comes
        # from useParams against ``/<key>/$id/edit``.
        assert "export function UpdateProjectsForm()" in out
        assert "useParams" in out
        assert 'from: "/projects/$id/edit"' in out

    def test_mutation_passes_path_id(self) -> None:
        out = self._out()

        assert "path: { id }" in out

    def test_success_toast_uses_updated_verb(self) -> None:
        out = self._out()

        assert "Project updated." in out
