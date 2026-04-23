"""Paginate extension: emits Page schema, wires pagination into search.

Runs at modifier scope with ``type: "paginate"`` as a nested
child of a list op.  Emits the ``{Model}Page`` response schema,
swaps the parent list's response model from ``list[{Model}ListItem]``
to ``{Model}Page``, and stamps the pagination mode plus
keyset/offset defaults onto the parent's search handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from kiln.config.schema import PaginateConfig
from kiln.operations._list_extension import find_list_outputs
from kiln.operations.types import SchemaClass, TestCase

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import ModifierConfig


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
        ctx: BuildContext[ModifierConfig],
        options: PaginateConfig,
    ) -> Iterable[object]:
        """Emit Page schema and amend List's outputs.

        Args:
            ctx: Build context for the ``"paginate"`` op entry.
            options: Parsed :class:`PaginateConfig`.

        Yields:
            ``{Model}Page`` response schema.

        """
        outputs = find_list_outputs(ctx)
        model = outputs.model

        list_item_name = f"{model.pascal}ListItem"
        page_name = model.suffixed("Page")
        yield SchemaClass(
            name=page_name,
            body_template="fastapi/schema_parts/page.py.j2",
            body_context={
                "model_name": model.pascal,
                "item_type": list_item_name,
                "mode": options.mode,
            },
        )

        handler = outputs.handler
        handler.response_model = page_name
        handler.return_type = page_name
        handler.body_context["pagination_mode"] = options.mode
        handler.body_context["max_page_size"] = options.max_page_size
        handler.body_context["cursor_field"] = options.cursor_field
        handler.extra_imports.append(
            (
                "ingot",
                "apply_keyset_pagination"
                if options.mode == "keyset"
                else "apply_offset_pagination",
            ),
        )

        search_request = outputs.search_request
        search_request.body_context["pagination_mode"] = options.mode
        search_request.body_context["default_page_size"] = (
            options.default_page_size
        )

        # The parent list's test case was emitted with
        # is_list_response=True because List doesn't know whether a
        # modifier will wrap the response in a Page.  Now that we
        # know, fix it.
        parent_id = ctx.store.ancestor_id_of(ctx.instance_id, "operation")
        if parent_id is not None:
            for tc in ctx.store.outputs_from(parent_id, "list", TestCase):
                tc.is_list_response = False
