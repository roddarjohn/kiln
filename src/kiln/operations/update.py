"""Update operation: PATCH /{pk} -- partially update a resource."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from foundry.naming import Name
from foundry.operation import operation
from kiln.config.schema import PYTHON_TYPES
from kiln.operations.renderers import utils_imports
from kiln.operations.types import (
    Field,
    FieldsOptions,
    RouteHandler,
    RouteParam,
    SchemaClass,
    TestCase,
    _field_dicts,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import (
        OperationConfig,
        ProjectConfig,
        ResourceConfig,
    )


@operation(
    "update",
    scope="operation",
    dispatch_on="name",
    requires=["create"],
)
class Update:
    """PATCH /{pk} -- partially update a resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for PATCH /{pk}.

        Args:
            ctx: Build context for the ``"update"`` operation entry.
            options: Parsed :class:`FieldsOptions`.

        Yields:
            The ``{Model}UpdateRequest`` schema (all fields
            optional), the route handler, and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
        request_schema = model.suffixed("UpdateRequest")

        yield SchemaClass(
            name=request_schema,
            fields=[
                Field(name=f.name, py_type=f.py_type, optional=True)
                for f in _field_dicts(options.fields)
            ],
            doc=f"Request body for updating a {model.pascal}.",
        )

        yield RouteHandler(
            method="PATCH",
            path=f"/{{{resource.pk}}}",
            function_name=f"update_{model.lower}",
            op_name=ctx.instance.name,
            params=[
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                ),
                RouteParam(name="body", annotation=request_schema),
            ],
            doc=f"Update a {model.pascal} by {resource.pk}.",
            request_schema=request_schema,
            body_template="fastapi/ops/update.py.j2",
            extra_imports=[("sqlalchemy", "update"), *utils_imports()],
        )

        yield TestCase(
            op_name="update",
            method="patch",
            path=f"/{{{resource.pk}}}",
            status_success=200,
            status_not_found=404,
            status_invalid=422,
            has_request_body=True,
            request_schema=request_schema,
        )
