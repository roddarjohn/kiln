"""Resource-scope op emitting the resource-level ``POST /_values``.

When a resource sets
:attr:`~be.config.schema.ResourceConfig.searchable` it gets a
single ``POST /_values`` route returning items shaped by the
resource's :class:`~be.config.schema.LinkConfig`.  Powers ``ref``
filter inputs on other resources and any FE "search this table"
affordance.  The resource's ``link`` attribute is required;
the cross-resource validator on :class:`~be.config.schema.ProjectConfig`
catches missing links at config-load time.

For shorthand link configs that name a string-typed display
attribute (``kind: "name"`` / ``"id_name"``) the search filters by
``ILIKE %q%`` on that column.  For builder-only configs and
``kind: "id"`` the ``q`` parameter is ignored and the route just
paginates the resource — the user can layer their own filtering
through the parent list endpoint when richer search is needed.
"""

from typing import TYPE_CHECKING

from be.operations._naming import app_module_for
from be.operations.types import RouteHandler, RouteParam, TestCase
from foundry.naming import Name, prefix_import
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("searchable", scope="resource")
class Searchable:
    """Emit ``POST /_values`` for resources that opt in.

    Gated by :attr:`~be.config.schema.ResourceConfig.searchable`;
    when unset, :meth:`when` returns ``False`` and nothing is
    emitted.  The cross-resource validator on
    :class:`~be.config.schema.ProjectConfig` guarantees
    :attr:`~be.config.schema.ResourceConfig.link` is set
    whenever this op fires.
    """

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run only when the resource opts in."""
        return ctx.instance.searchable

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Emit the resource-level value-provider route.

        Args:
            ctx: Build context for the resource.
            _options: Unused — no per-resource config beyond the
                opt-in.

        Yields:
            One :class:`~be.operations.types.RouteHandler` plus a
            matching :class:`~be.operations.types.TestCase`.

        """
        resource = ctx.instance
        model_module, model = Name.from_dotted(resource.model)

        link = resource.link

        if link is None:  # pragma: no cover -- validator catches this
            msg = (
                f"Resource {resource.model!r} has searchable=True but "
                f"no link config; cross-resource validator should "
                f"have caught this."
            )
            raise ValueError(msg)

        # Explicit search.fields wins; fall back to link.name when
        # shorthand (the common case) and skip q-filtering when
        # neither is available (builder-only / id-only links).
        if resource.search is not None:
            search_fields: list[str] = list(resource.search.fields)

        elif link.builder is None and link.name:
            search_fields = [link.name]

        else:
            search_fields = []

        links_module = prefix_import(
            ctx.package_prefix,
            app_module_for(resource.model),
            "links",
        )

        body_context: dict[str, object] = {
            "model_name": model.pascal,
            "slug": model.lower,
            "search_fields": search_fields,
        }

        yield RouteHandler(
            method="POST",
            path="/_values",
            function_name=f"values_{model.lower}",
            op_name="searchable",
            params=[
                RouteParam(name="body", annotation="FilterValuesRequest"),
            ],
            return_type="dict[str, Any]",
            doc=(
                f"Resource-level search returning {model.pascal} "
                f"items shaped by the configured link schema."
            ),
            body_template="fastapi/ops/searchable.py.j2",
            body_context=body_context,
            extra_imports=[
                ("typing", "Any"),
                ("sqlalchemy", "select"),
                (model_module, model.pascal),
                ("ingot.filter_values", "FilterValuesRequest"),
                (links_module, "LINKS"),
            ],
        )

        yield TestCase(
            op_name="searchable",
            method="post",
            path="/_values",
            status_success=200,
            has_request_body=True,
            request_schema="FilterValuesRequest",
            action_name=f"values_{model.lower}",
        )
