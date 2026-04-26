"""Get operation: GET /{pk} -- retrieve a single resource."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from foundry.naming import Name
from foundry.operation import operation
from kiln.config.schema import PYTHON_TYPES
from kiln.operations.renderers import utils_imports
from kiln.operations.types import (
    FieldsOptions,
    RouteHandler,
    RouteParam,
    TestCase,
    _construct_dump,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import (
        OperationConfig,
        ProjectConfig,
        ResourceConfig,
    )


@operation("get", scope="operation", dispatch_on="name")
class Get:
    """GET /{pk} -- retrieve a single resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for GET /{pk}.

        Args:
            ctx: Build context for the ``"get"`` operation entry.
            options: Parsed :class:`FieldsOptions`.

        Yields:
            The ``{Model}Resource`` schema, its serializer, the
            route handler, and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        model_module, model = Name.from_dotted(resource.model)

        dump = _construct_dump(
            model,
            model_module,
            options.fields,
            suffix="Resource",
            stem="resource",
        )

        # Nested sub-schemas / sub-serializers are ordered deepest-first
        # so they render before the parent class that references them.
        yield from dump.nested_schemas
        yield dump.main_schema
        yield from dump.nested_serializers
        yield dump.main_serializer

        yield RouteHandler(
            method="GET",
            path=f"/{{{resource.pk}}}",
            function_name=f"get_{model.lower}",
            op_name=ctx.instance.name,
            params=[
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                )
            ],
            response_model=dump.main_schema.name,
            serializer_fn=dump.main_serializer.function_name,
            return_type=dump.main_schema.name,
            doc=f"Get a {model.pascal} by {resource.pk}.",
            body_template="fastapi/ops/get.py.j2",
            body_context={"load_options": dump.load_options},
            extra_imports=[
                ("sqlalchemy", "select"),
                *utils_imports(),
                *dump.load_imports,
            ],
        )

        yield TestCase(
            op_name="get",
            method="get",
            path=f"/{{{resource.pk}}}",
            status_success=200,
            status_not_found=404,
            response_schema=dump.main_schema.name,
        )
