"""Create operation: POST / -- create a new resource."""

from typing import TYPE_CHECKING, cast

from be.operations.renderers import gate_wiring, hooks_wiring
from be.operations.representations import pick_representation
from be.operations.types import (
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
    "create",
    scope="operation",
    dispatch_on="name",
)
class Create:
    """POST / -- create a new resource.

    When :attr:`OperationConfig.representation` is set, the handler
    captures the just-written row via ``.returning(Model)`` and
    runs it through the representation's builder so the response
    body carries the canonical resource shape.  Without
    ``representation``, the handler returns 201 with no body.

    Write ops do *not* inherit
    :attr:`ResourceConfig.default_representation` -- that
    inheritance would silently change every create on every
    rep-using resource from "201 no body" to "201 with body".
    Make the user spell ``representation:`` explicitly.
    """

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for POST /."""
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
        request_schema = model.suffixed("CreateRequest")
        field_dicts, enum_imports = _field_dicts(options.fields)

        yield SchemaClass(
            name=request_schema,
            fields=field_dicts,
            doc=f"Request body for creating a {model.pascal}.",
            extra_imports=enum_imports,
        )

        gate_ctx, gate_imports = gate_wiring(
            ctx.instance,
            resource,
            ctx.package_prefix,
            is_object_scope=False,
        )
        hook_ctx, hook_imports = hooks_wiring(ctx.instance)

        spec = pick_representation(ctx)
        rep_ctx = (
            {"serialize_response_call": spec.serializer_fn}
            if spec is not None
            else {}
        )

        yield RouteHandler(
            method="POST",
            path="/",
            function_name=f"create_{model.snake}",
            op_name=ctx.instance.name,
            params=[RouteParam(name="body", annotation=request_schema)],
            status_code=201,
            response_model=spec.schema_class if spec else None,
            response_schema_module=spec.schema_module if spec else None,
            return_type=spec.schema_class if spec else None,
            serializer_fn=spec.serializer_fn if spec else None,
            serializer_fn_module=spec.serializer_fn_module if spec else None,
            doc=f"Create a new {model.pascal}.",
            request_schema=request_schema,
            body_template="fastapi/ops/create.py.j2",
            body_context={**gate_ctx, **hook_ctx, **rep_ctx},
            extra_imports=[
                ("sqlalchemy", "insert"),
                *gate_imports,
                *hook_imports,
            ],
        )

        yield TestCase(
            op_name="create",
            method="post",
            path="/",
            status_success=201,
            status_invalid=422,
            has_request_body=True,
            request_schema=request_schema,
            # _field_dicts above already rejects nested FieldSpecs;
            # ``field_dicts`` carries the rendered py_type (scalar
            # mapping or enum class name).
            request_fields=[
                {"name": f.name, "py_type": f.py_type} for f in field_dicts
            ],
        )
