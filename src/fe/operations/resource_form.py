"""Project-scope op: per-resource create/update form components.

For every resource that declares a ``create`` (and / or
``update``) section, this op emits the matching form
components:

* ``src/{key}/Create{Pascal}Form.tsx`` -- wraps the openapi-ts
  ``create_fn`` mutation; rendered inside the list page's
  ``<DrawerTrigger><Drawer>`` (the parent owns the open / close
  state and passes a ``close`` callback via render-prop).
* ``src/{key}/Update{Pascal}Form.tsx`` -- analogous, for
  ``update_fn``.

The form is plain controlled state today (no react-hook-form
yet) -- one ``useState`` per field, each field a glaze
``<TextField>``, submission via a React-Query ``useMutation``
that invalidates ``[<key>]`` on success.  When more field types
or validation are needed we'll layer on a richer form pipeline,
but the simple shape covers the demo cases (CRUD with all-string
bodies) without pulling in deps.
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

        for key, resource in config.resources.items():
            pascal = _pascal(key)

            if resource.create is not None and resource.create_fn is not None:
                yield self._form(
                    key=key,
                    pascal=pascal,
                    label_singular=resource.label.singular,
                    verb="Create",
                    fn=resource.create_fn,
                    request_type=resource.create_request_type,
                    form=resource.create,
                )

            if resource.update is not None and resource.update_fn is not None:
                yield self._form(
                    key=key,
                    pascal=pascal,
                    label_singular=resource.label.singular,
                    verb="Update",
                    fn=resource.update_fn,
                    request_type=resource.update_request_type,
                    form=resource.update,
                )

    def _form(  # noqa: PLR0913
        self,
        *,
        key: str,
        pascal: str,
        label_singular: str,
        verb: str,
        fn: str,
        request_type: str | None,
        form: FormConfig,
    ) -> StaticFile:
        """Build a single Create / Update form file."""
        # The mutation needs path params for update; for create
        # there are none.  The template branches on `verb`.
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
                "request_type": request_type,
                "fields": [
                    {"name": f, "label": _humanize(f)} for f in form.fields
                ],
            },
        )
