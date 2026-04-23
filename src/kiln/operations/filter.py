"""Filter extension: emits filter schemas, wires them into search.

Runs at modifier scope with ``type: "filter"`` as a nested child
of a list op.  Emits the ``{Model}FilterCondition`` and
``{Model}FilterExpression`` schemas, then flips ``has_filter`` on
the parent list's ``SearchRequest`` and search handler so the
generated route calls ``ingot.apply_filters``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from kiln.config.schema import FilterConfig, OperationConfig
from kiln.operations.list import (
    find_search_handler,
    find_search_request,
    resource_model,
)
from kiln.operations.types import SchemaClass

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import ModifierConfig


@operation(
    "filter",
    scope="modifier",
    dispatch_on="type",
)
class Filter:
    """Amend the list op with filterable fields.

    Runs at modifier scope — filter configs nest inside a specific
    list op, so the engine descends into List first, then into its
    modifiers in order.  No sibling lookup, no ambiguity with
    multiple lists per resource.
    """

    Options = FilterConfig

    def build(
        self,
        ctx: BuildContext[ModifierConfig],
        options: FilterConfig,
    ) -> Iterable[object]:
        """Emit filter schemas and amend List's outputs.

        Args:
            ctx: Build context for the ``"filter"`` op entry.
            options: Parsed :class:`FilterConfig`.

        Yields:
            ``{Model}FilterCondition`` schema.  (The expression
            schema is rendered as part of the same template.)

        """
        model = resource_model(ctx)

        allowed = options.fields or _list_field_names(ctx)
        yield SchemaClass(
            name=model.suffixed("FilterCondition"),
            body_template="fastapi/schema_parts/filter_node.py.j2",
            body_context={
                "model_name": model.pascal,
                "allowed_fields": allowed,
            },
            extra_imports=[
                ("typing", "Any"),
                ("typing", "Literal"),
                ("pydantic", "ConfigDict"),
                ("pydantic", "Field"),
            ],
        )

        search_request = find_search_request(ctx)
        search_request.body_context["has_filter"] = True

        handler = find_search_handler(ctx)
        handler.body_context["has_filter"] = True
        handler.extra_imports.append(("ingot", "apply_filters"))


def _list_field_names(ctx: BuildContext[ModifierConfig]) -> list[str]:
    """Return the parent list op's declared field names.

    When a filter config doesn't name fields explicitly, every
    field the list op exposes becomes filterable.  The modifier's
    parent scope instance *is* the list op's config, so we read
    its ``options["fields"]`` directly — no sibling scan.
    """
    parent = ctx.store.ancestor_of(ctx.instance_id, "operation")
    if not isinstance(parent, OperationConfig):
        return []
    fields: list[dict[str, str]] = parent.options.get("fields") or []
    return [f["name"] for f in fields if isinstance(f, dict)]
