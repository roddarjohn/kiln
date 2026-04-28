"""Update operation: PATCH /{pk} -- partially update a resource."""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations.renderers import gate_wiring, utils_imports
from be.operations.types import (
    Field,
    FieldsOptions,
    RouteHandler,
    RouteParam,
    SchemaClass,
    TestCase,
    _field_dicts,
)
from foundry.naming import Name
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import (
        OperationConfig,
        ProjectConfig,
        ResourceConfig,
    )
    from foundry.engine import BuildContext


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
            options: Parsed :class:`~be.operations.types.FieldsOptions`.

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

        gate_ctx, gate_imports = gate_wiring(
            ctx.instance,
            resource,
            ctx.package_prefix,
            is_object_scope=True,
        )
        # Gated update needs to fetch the row first so the guard
        # can inspect resource state -- a one-shot UPDATE doesn't
        # surface the row.  Pulled in unconditionally on the gated
        # path; the ungated path keeps the single-statement form.
        gate_extra_imports = [("sqlalchemy", "select")] if gate_ctx else []

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
            body_context=gate_ctx,
            extra_imports=[
                ("sqlalchemy", "update"),
                *utils_imports(),
                *gate_extra_imports,
                *gate_imports,
            ],
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
