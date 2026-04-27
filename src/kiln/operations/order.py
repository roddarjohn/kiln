"""Order extension: emits sort schemas, wires them into search.

Runs at modifier scope with ``type: "order"`` as a nested child
of a list op.  Emits the ``{Model}SortField`` enum and
``{Model}SortClause`` schema, and stamps the sort defaults onto
the parent list's search handler.
"""

from typing import TYPE_CHECKING

from foundry.operation import operation
from kiln.config.schema import OrderConfig
from kiln.operations.list import ListResult, resource_model
from kiln.operations.types import EnumClass, SchemaClass

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import ModifierConfig, ProjectConfig


@operation(
    "order",
    scope="modifier",
    dispatch_on="type",
)
class Order:
    """Amend the list op with sort fields."""

    Options = OrderConfig

    def build(
        self,
        ctx: BuildContext[ModifierConfig, ProjectConfig],
        options: OrderConfig,
    ) -> Iterable[object]:
        """Emit sort schemas and amend List's outputs.

        Args:
            ctx: Build context for the ``"order"`` op entry.
            options: Parsed :class:`~kiln.config.schema.OrderConfig`.

        Yields:
            ``{Model}SortField`` enum and ``{Model}SortClause``
            schema.

        """
        model = resource_model(ctx)

        yield EnumClass(
            name=model.suffixed("SortField"),
            members=[(f.upper(), f) for f in options.fields],
            base="str, Enum",
        )

        yield SchemaClass(
            name=model.suffixed("SortClause"),
            body_template="fastapi/schema_parts/sort_clause.py.j2",
            body_context={"model_name": model.pascal},
            extra_imports=[("typing", "Literal")],
        )

        result = ctx.store.output_under_ancestor(
            ctx.instance_id, "operation", ListResult
        )
        result.search_request.body_context["has_sort"] = True

        handler = result.handler
        handler.body_context["has_sort"] = True

        if options.default is not None:
            handler.body_context["default_sort_field"] = options.default

        handler.body_context["default_sort_dir"] = options.default_dir
        handler.extra_imports.append(("ingot.ordering", "apply_ordering"))
