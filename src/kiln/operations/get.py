"""Get operation: GET /{pk} -- retrieve a single resource."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from foundry.naming import Name
from foundry.operation import operation
from kiln._helpers import PYTHON_TYPES
from kiln.operations._shared import (
    FieldsOptions,
    _construct_response_schema,
    _construct_serializer,
)
from kiln.operations.renderers import utils_imports
from kiln.operations.types import RouteHandler, RouteParam, TestCase

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import OperationConfig, ResourceConfig


@operation("get", scope="operation", dispatch_on="name")
class Get:
    """GET /{pk} -- retrieve a single resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig],
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
        _, model = Name.from_dotted(resource.model)

        schema = _construct_response_schema(
            model, options.fields, suffix="Resource"
        )
        serializer = _construct_serializer(model, schema, stem="resource")

        yield schema
        yield serializer

        yield RouteHandler(
            method="GET",
            path=f"/{{{resource.pk}}}",
            function_name=f"get_{model.lower}",
            params=[
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                )
            ],
            response_model=schema.name,
            serializer_fn=serializer.function_name,
            return_type=schema.name,
            doc=f"Get a {model.pascal} by {resource.pk}.",
            body_template="fastapi/ops/get.py.j2",
            extra_imports=[("sqlalchemy", "select"), *utils_imports()],
        )

        yield TestCase(
            op_name="get",
            method="get",
            path=f"/{{{resource.pk}}}",
            status_success=200,
            status_not_found=404,
            response_schema=schema.name,
        )
