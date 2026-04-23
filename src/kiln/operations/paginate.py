"""Paginate extension: emits Page schema, wires pagination into search.

Runs at operation scope with ``type: "paginate"`` after
:class:`~kiln.operations.list.List`.  Emits the ``{Model}Page``
response schema, swaps List's response model from
``list[{Model}ListItem]`` to ``{Model}Page``, and stamps the
pagination mode plus keyset/offset defaults onto List's search
handler.
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
    from kiln.config.schema import OperationConfig


@operation(
    "paginate",
    scope="operation",
    dispatch_on="type",
    requires=["list"],
)
class Paginate:
    """Amend the list op with pagination."""

    Options = PaginateConfig

    def build(
        self,
        ctx: BuildContext[OperationConfig],
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

        # The list test case was emitted with is_list_response=True
        # because List doesn't know whether anyone will wrap the
        # response in a Page.  Now that we know, fix it.
        resource_id = ctx.store.ancestor_id_of(ctx.instance_id, "resource")
        if resource_id is not None:
            for tc in ctx.store.outputs_under(resource_id, TestCase):
                if tc.op_name == "list":
                    tc.is_list_response = False
