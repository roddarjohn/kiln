"""Filter extension: emits filter schemas, wires them into search.

Runs at operation scope with ``type: "filter"`` after
:class:`~kiln.operations.list.List`.  Emits the
``{Model}FilterCondition`` and ``{Model}FilterExpression``
schemas, then flips ``has_filter`` on List's ``SearchRequest``
and search handler so the generated route calls
``ingot.apply_filters``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from kiln.config.schema import FilterConfig
from kiln.operations._list_extension import find_list_outputs
from kiln.operations.types import SchemaClass

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import OperationConfig


@operation(
    "filter",
    scope="operation",
    dispatch_on="type",
    requires=["list"],
)
class Filter:
    """Amend the list op with filterable fields.

    ``filter`` config entries stand next to the list op at the
    same resource; the engine topo-sorts Filter after List.
    """

    Options = FilterConfig

    def build(
        self,
        ctx: BuildContext[OperationConfig],
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
        outputs = find_list_outputs(ctx)
        model = outputs.model

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

        outputs.search_request.body_context["has_filter"] = True
        outputs.handler.body_context["has_filter"] = True
        outputs.handler.extra_imports.append(("ingot", "apply_filters"))


def _list_field_names(ctx: BuildContext[OperationConfig]) -> list[str]:
    """Return the List op's field names for this resource.

    When a filter config doesn't name fields explicitly, every
    field the list op exposes becomes filterable.  The names live
    on the ``name: "list"`` entry's ``options["fields"]``.
    """
    from kiln.config.schema import ResourceConfig  # noqa: PLC0415

    resource = ctx.store.ancestor_of(ctx.instance_id, "resource")
    if not isinstance(resource, ResourceConfig) or resource.operations is None:
        return []

    for op in resource.operations:
        if not isinstance(op, str) and op.name == "list":
            fields: list[dict[str, str]] = op.options.get("fields") or []
            return [f["name"] for f in fields if isinstance(f, dict)]
    return []
