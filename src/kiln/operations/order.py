"""Order extension: emits sort schemas, wires them into search.

Runs at modifier scope with ``type: "order"`` as a nested child
of a list op.  Emits the ``{Model}SortField`` enum and
``{Model}SortClause`` schema, and stamps the sort defaults onto
the parent list's search handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from kiln.config.schema import OrderConfig
from kiln.operations._list_extension import find_list_outputs
from kiln.operations.types import EnumClass, SchemaClass

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import ModifierConfig


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
        ctx: BuildContext[ModifierConfig],
        options: OrderConfig,
    ) -> Iterable[object]:
        """Emit sort schemas and amend List's outputs.

        Args:
            ctx: Build context for the ``"order"`` op entry.
            options: Parsed :class:`OrderConfig`.

        Yields:
            ``{Model}SortField`` enum and ``{Model}SortClause``
            schema.

        """
        outputs = find_list_outputs(ctx)
        model = outputs.model

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

        outputs.search_request.body_context["has_sort"] = True
        outputs.handler.body_context["has_sort"] = True
        if options.default is not None:
            outputs.handler.body_context["default_sort_field"] = options.default
        outputs.handler.body_context["default_sort_dir"] = options.default_dir
        outputs.handler.extra_imports.append(("ingot", "apply_ordering"))
