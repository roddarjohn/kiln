"""Update operation: PATCH /{pk} -- partially update a resource."""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations.renderers import (
    FETCH_OR_404_IMPORT,
    gate_wiring,
    hooks_wiring,
)
from be.operations.representations import pick_representation
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
)
class Update:
    """PATCH /{pk} -- partially update a resource.

    Same response semantics as :class:`~be.operations.create.Create`:
    set :attr:`OperationConfig.representation` to capture the
    updated row via ``.returning(Model)`` and return the rep
    through its builder; leave it unset for today's no-body 200.
    Write ops do not inherit
    :attr:`ResourceConfig.default_representation`.
    """

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for PATCH /{pk}."""
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
        request_schema = model.suffixed("UpdateRequest")
        field_dicts, enum_imports = _field_dicts(options.fields)

        yield SchemaClass(
            name=request_schema,
            fields=[
                Field(name=f.name, py_type=f.py_type, optional=True)
                for f in field_dicts
            ],
            doc=f"Request body for updating a {model.pascal}.",
            extra_imports=enum_imports,
        )

        gate_ctx, gate_imports = gate_wiring(
            ctx.instance,
            resource,
            ctx.package_prefix,
            is_object_scope=True,
        )
        hook_ctx, hook_imports = hooks_wiring(ctx.instance)

        spec = pick_representation(ctx)
        rep_ctx = (
            {"serialize_response_call": spec.serializer_fn}
            if spec is not None
            else {}
        )

        yield RouteHandler(
            method="PATCH",
            path=f"/{{{resource.pk.name}}}",
            function_name=f"update_{model.snake}",
            op_name=ctx.instance.name,
            params=[
                RouteParam(
                    name=resource.pk.name,
                    annotation=PYTHON_TYPES[resource.pk.type],
                ),
                RouteParam(name="body", annotation=request_schema),
            ],
            response_model=spec.schema_class if spec else None,
            response_schema_module=spec.schema_module if spec else None,
            return_type=spec.schema_class if spec else None,
            serializer_fn=spec.serializer_fn if spec else None,
            serializer_fn_module=spec.serializer_fn_module if spec else None,
            doc=f"Update a {model.pascal} by {resource.pk.name}.",
            request_schema=request_schema,
            body_template="fastapi/ops/update.py.j2",
            body_context={**gate_ctx, **hook_ctx, **rep_ctx},
            extra_imports=[
                ("sqlalchemy", "select"),
                ("sqlalchemy", "update"),
                FETCH_OR_404_IMPORT,
                *gate_imports,
                *hook_imports,
            ],
        )

        yield TestCase(
            op_name="update",
            method="patch",
            path=f"/{{{resource.pk.name}}}",
            status_success=200,
            status_not_found=404,
            status_invalid=422,
            has_request_body=True,
            request_schema=request_schema,
        )
