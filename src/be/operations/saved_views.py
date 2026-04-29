"""Resource-scope op emitting saved-view CRUD endpoints.

Gated by :attr:`~be.config.schema.ResourceConfig.saved_views`.
Each opted-in resource gets per-user CRUD over named filter+sort
states stored in :class:`ingot.saved_views.SavedView`.

Generated routes:

* ``GET /views`` — list the caller's saved views for this
  resource, with ref filter values hydrated through the
  per-app ``LINKS`` registry.
* ``POST /views`` — create a saved view from a
  ``SavedViewCreate`` body.
* ``GET /views/{id}`` — fetch a single saved view by id.
* ``PATCH /views/{id}`` — partial update.
* ``DELETE /views/{id}`` — delete (per-user scoped).

The cross-resource validator on
:class:`~be.config.schema.ProjectConfig` guarantees a
:attr:`~be.config.schema.ResourceConfig.link` is set whenever
this op fires.
"""

from typing import TYPE_CHECKING

from be.operations.types import RouteHandler, RouteParam, TestCase
from foundry.naming import Name
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("saved_views", scope="resource")
class SavedViews:
    """Emit saved-view CRUD for resources that opt in."""

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run only when the resource opts in."""
        return ctx.instance.saved_views

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Emit the five CRUD route handlers + matching TestCases."""
        resource = ctx.instance
        _, model = Name.from_dotted(resource.model)
        slug = model.lower

        common_imports: list[tuple[str, str]] = [
            ("typing", "Any"),
            ("fastapi", "HTTPException"),
            ("sqlalchemy", "select"),
            ("ingot.saved_views", "SavedView"),
            ("ingot.saved_views", "SavedViewCreate"),
            ("ingot.saved_views", "SavedViewUpdate"),
            ("ingot.saved_views", "dump_view"),
        ]

        common_body_context: dict[str, object] = {
            "slug": slug,
            "model_name": model.pascal,
        }

        # Collection: GET /views, POST /views
        yield RouteHandler(
            method="GET",
            path="/views",
            function_name=f"saved_views_{slug}_list",
            op_name="saved_views",
            params=[],
            return_type="list[dict[str, Any]]",
            doc=f"List the caller's saved views for {model.pascal}.",
            body_template="fastapi/ops/saved_views_list.py.j2",
            body_context=common_body_context,
            extra_imports=common_imports,
        )

        yield RouteHandler(
            method="POST",
            path="/views",
            function_name=f"saved_views_{slug}_create",
            op_name="saved_views",
            params=[
                RouteParam(name="body", annotation="SavedViewCreate"),
            ],
            return_type="dict[str, Any]",
            doc=f"Create a saved view for {model.pascal}.",
            body_template="fastapi/ops/saved_views_create.py.j2",
            body_context=common_body_context,
            extra_imports=common_imports,
        )

        # Object: GET /views/{view_id}, PATCH ..., DELETE ...
        view_pk_param = RouteParam(name="view_id", annotation="str")

        yield RouteHandler(
            method="GET",
            path="/views/{view_id}",
            function_name=f"saved_views_{slug}_get",
            op_name="saved_views",
            params=[view_pk_param],
            return_type="dict[str, Any]",
            doc=f"Fetch one saved view for {model.pascal} by id.",
            body_template="fastapi/ops/saved_views_get.py.j2",
            body_context=common_body_context,
            extra_imports=common_imports,
        )

        yield RouteHandler(
            method="PATCH",
            path="/views/{view_id}",
            function_name=f"saved_views_{slug}_update",
            op_name="saved_views",
            params=[
                view_pk_param,
                RouteParam(name="body", annotation="SavedViewUpdate"),
            ],
            return_type="dict[str, Any]",
            doc=f"Update a saved view for {model.pascal}.",
            body_template="fastapi/ops/saved_views_update.py.j2",
            body_context=common_body_context,
            extra_imports=common_imports,
        )

        yield RouteHandler(
            method="DELETE",
            path="/views/{view_id}",
            function_name=f"saved_views_{slug}_delete",
            op_name="saved_views",
            params=[view_pk_param],
            status_code=204,
            return_type="None",
            doc=f"Delete a saved view for {model.pascal}.",
            body_template="fastapi/ops/saved_views_delete.py.j2",
            body_context=common_body_context,
            extra_imports=common_imports,
        )

        for method, path, action_name in (
            ("get", "/views", "list"),
            ("post", "/views", "create"),
            ("get", "/views/{view_id}", "get"),
            ("patch", "/views/{view_id}", "update"),
            ("delete", "/views/{view_id}", "delete"),
        ):
            yield TestCase(
                op_name="saved_views",
                method=method,
                path=path,
                status_success=200 if action_name != "delete" else 204,
                has_request_body=method in {"post", "patch"},
                request_schema=(
                    "SavedViewCreate"
                    if action_name == "create"
                    else "SavedViewUpdate"
                    if action_name == "update"
                    else None
                ),
                action_name=f"saved_views_{action_name}",
            )
