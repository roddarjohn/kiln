"""Project-scope op: per-resource create/update form components.

For every resource that declares a ``create`` (and / or
``update``) section, this op emits the matching form
components:

- ``src/{key}/Create{Pascal}Form.tsx`` -- mounted at the
  ``/<key>/new`` route.
- ``src/{key}/Update{Pascal}Form.tsx`` -- mounted at the
  ``/<key>/$id/edit`` route.  Requires ``get_fn`` (used to
  pre-populate the form).

Both forms ride glaze's ``useFormMutation`` -- a React-Hook-Form
+ React-Query adapter that collects the FormData at submit time
and dispatches the openapi-ts mutation.  No per-field
``useState`` boilerplate; each ``<TextField>`` is uncontrolled
with a ``name`` matching the request body shape.

Update forms additionally call ``useSuspenseQuery`` against the
openapi-ts ``*Options`` helper to fetch the existing resource;
the route's ``pendingComponent`` (a glaze ``<PageLoader>``)
covers the load.  ``defaultValue`` on each TextField pre-fills
the field with the current value.

Cancel and the back link both pop the browser history so the
user lands wherever they came from (list, detail, etc.).  On
success we toast, invalidate ``[<key>]``, and pop history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from fe.config import FormConfig, ProjectConfig
    from foundry.engine import BuildContext


def _pascal(key: str) -> str:
    parts = [p for p in key.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _humanize(field: str) -> str:
    return " ".join(p[:1].upper() + p[1:] for p in field.split("_"))


@operation("resource_form", scope="project")
class ResourceForm:
    """Emit Create / Update form components per resource."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield form .tsx files for every configured form."""
        config = ctx.instance

        id_prefix = "/_app" if config.auth is not None else ""

        for key, resource in config.resources.items():
            pascal = _pascal(key)

            if resource.create is not None and resource.create_fn is not None:
                yield self._form(
                    key=key,
                    pascal=pascal,
                    label_singular=resource.label.singular,
                    verb="Create",
                    fn=resource.create_fn,
                    get_fn=None,
                    request_type=resource.create_request_type,
                    form=resource.create,
                    id_prefix=id_prefix,
                )

            # Update needs ``get_fn`` to pre-populate the form;
            # without it we'd render blank fields and silently
            # blow away whatever's stored.
            if (
                resource.update is not None
                and resource.update_fn is not None
                and resource.get_fn is not None
            ):
                yield self._form(
                    key=key,
                    pascal=pascal,
                    label_singular=resource.label.singular,
                    verb="Update",
                    fn=resource.update_fn,
                    get_fn=resource.get_fn,
                    request_type=resource.update_request_type,
                    form=resource.update,
                    id_prefix=id_prefix,
                )

    def _form(  # noqa: PLR0913
        self,
        *,
        key: str,
        pascal: str,
        label_singular: str,
        verb: str,
        fn: str,
        get_fn: str | None,
        request_type: str | None,
        form: FormConfig,
        id_prefix: str,
    ) -> StaticFile:
        """Build a single Create / Update form file."""
        # Routes for the form pages live alongside list / detail
        # at well-known paths so the form can navigate ``back`` on
        # cancel / success.
        list_path = f"/{key}"
        detail_path = f"/{key}/$id"
        form_path = f"/{key}/new" if verb == "Create" else f"/{key}/$id/edit"

        return StaticFile(
            path=f"src/{key}/{verb}{pascal}Form.tsx",
            template="src/resource/Form.tsx.j2",
            context={
                "key": key,
                "pascal": pascal,
                "label_singular": label_singular,
                "verb": verb,
                "verb_lower": verb.lower(),
                "fn": fn,
                "get_fn": get_fn,
                "request_type": request_type,
                "fields": [
                    {"name": f, "label": _humanize(f)} for f in form.fields
                ],
                "list_path": list_path,
                "detail_path": detail_path,
                "form_path": form_path,
                # Full TSR route id (with the auth-layout prefix
                # when auth is on) -- used by useParams.
                "form_route_id": f"{id_prefix}{form_path}",
            },
        )
