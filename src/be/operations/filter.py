"""Filter modifier: emits the per-list FilterCondition schema only.

Runs at modifier scope with ``type: "filter"`` as a nested child of
a list op.  Emits the
``{Model}FilterCondition`` Pydantic schema, then flips
``has_filter`` on the parent list's request schema and search
handler so the generated route calls
:func:`ingot.filters.apply_filters` against the parsed filter tree.

Discovery (``POST /_filters``) and value providers
(``POST /_values``) are *not* emitted here — those land on the
project-wide router in :mod:`be.operations.resource_registry`,
which walks every resource's filter modifier at project scope and
emits a single :class:`ingot.resource_registry.ResourceRegistry`-
backed endpoint set.
"""

from typing import TYPE_CHECKING

from be.config.schema import FilterConfig, ProjectConfig
from be.operations.list import ListResult, resource_model
from be.operations.types import SchemaClass
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import ModifierConfig
    from foundry.engine import BuildContext


@operation(
    "filter",
    scope="modifier",
    dispatch_on="type",
)
class Filter:
    """Amend the list op with the FilterCondition schema.

    Runs at modifier scope so the engine descends into List first
    and finds this modifier as one of its children.  Mutates the
    parent list op's :class:`~be.operations.list.ListResult` in
    place; emits the FilterCondition schema as a separate output.
    """

    Options = FilterConfig

    def build(
        self,
        ctx: BuildContext[ModifierConfig, ProjectConfig],
        options: FilterConfig,
    ) -> Iterable[object]:
        """Emit the FilterCondition schema and flip has_filter.

        Args:
            ctx: Build context for the ``"filter"`` op entry.
            options: Parsed :class:`~be.config.schema.FilterConfig`.

        Yields:
            One :class:`~be.operations.types.SchemaClass` for the
            FilterCondition Pydantic model.  The matching
            FilterExpression renders inline in the same template.

        """
        model = resource_model(ctx)
        result = ctx.store.output_under_ancestor(
            ctx.instance_id, "operation", ListResult
        )
        allowed = [f.name for f in options.fields]

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

        result.search_request.body_context["has_filter"] = True
        result.handler.body_context["has_filter"] = True
        result.handler.extra_imports.append(
            ("ingot.filters", "apply_filters"),
        )
