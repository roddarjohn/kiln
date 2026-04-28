"""Paginate extension: emits Page schema, wires pagination into search.

Runs at modifier scope with ``type: "paginate"`` as a nested
child of a list op.  Emits the ``{Model}Page`` response schema,
swaps the parent list's response model from ``list[{Model}ListItem]``
to ``{Model}Page``, and stamps the pagination mode plus
keyset/offset defaults onto the parent's search handler.
"""

from typing import TYPE_CHECKING

from be.config.schema import PaginateConfig
from be.operations.list import ListResult, resource_model
from be.operations.types import SchemaClass
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import ModifierConfig, ProjectConfig
    from foundry.engine import BuildContext


@operation(
    "paginate",
    scope="modifier",
    dispatch_on="type",
)
class Paginate:
    """Amend the list op with pagination."""

    Options = PaginateConfig

    def build(
        self,
        ctx: BuildContext[ModifierConfig, ProjectConfig],
        options: PaginateConfig,
    ) -> Iterable[object]:
        """Emit Page schema and amend List's outputs.

        Args:
            ctx: Build context for the ``"paginate"`` op entry.
            options: Parsed :class:`~be.config.schema.PaginateConfig`.

        Yields:
            ``{Model}Page`` response schema.

        """
        model = resource_model(ctx)

        page_name = model.suffixed("Page")
        yield SchemaClass(
            name=page_name,
            body_template="fastapi/schema_parts/page.py.j2",
            body_context={
                "model_name": model.pascal,
                "item_type": f"{model.pascal}ListItem",
                "mode": options.mode,
            },
        )

        result = ctx.store.output_under_ancestor(
            ctx.instance_id, "operation", ListResult
        )

        handler = result.handler
        handler.response_model = page_name
        handler.return_type = page_name
        handler.body_context["pagination_mode"] = options.mode
        handler.body_context["max_page_size"] = options.max_page_size
        handler.body_context["cursor_field"] = options.cursor_field
        handler.extra_imports.append(
            (
                "ingot.pagination",
                "apply_keyset_pagination"
                if options.mode == "keyset"
                else "apply_offset_pagination",
            ),
        )

        result.search_request.body_context["pagination_mode"] = options.mode
        result.search_request.body_context["default_page_size"] = (
            options.default_page_size
        )

        # The parent list's test case was emitted with
        # is_list_response=True because List doesn't know whether a
        # modifier will wrap the response in a Page.  Now that we
        # know, fix it.
        result.test_case.is_list_response = False
