"""Create operation: POST / -- create a new resource."""

from typing import TYPE_CHECKING, cast

from be.operations.links import (
    _representation_class_name,
    representation_fn_name,
)
from be.operations.renderers import gate_wiring, hooks_wiring
from be.operations.types import (
    FieldsOptions,
    RouteHandler,
    RouteParam,
    SchemaClass,
    TestCase,
    _field_dicts,
)
from foundry.naming import Name, prefix_import
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import (
        OperationConfig,
        ProjectConfig,
        RepresentationConfig,
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
    body carries the canonical resource shape.  When unset, the
    handler returns 201 with no body (today's behaviour).
    """

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for POST /.

        Yields:
            The ``{Model}CreateRequest`` schema, the route handler,
            and a test case.

        """
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

        rep = _resolve_response_representation(ctx.instance, resource)
        rep_ctx, rep_imports, response_model, response_schema_module = (
            _representation_response_wiring(
                ctx,
                resource,
                rep,
                model,
                package_prefix=ctx.package_prefix,
            )
        )

        yield RouteHandler(
            method="POST",
            path="/",
            function_name=f"create_{model.snake}",
            op_name=ctx.instance.name,
            params=[RouteParam(name="body", annotation=request_schema)],
            status_code=201,
            response_model=response_model,
            response_schema_module=response_schema_module,
            return_type=response_model,
            doc=f"Create a new {model.pascal}.",
            request_schema=request_schema,
            body_template="fastapi/ops/create.py.j2",
            body_context={**gate_ctx, **hook_ctx, **rep_ctx},
            extra_imports=[
                ("sqlalchemy", "insert"),
                *gate_imports,
                *hook_imports,
                *rep_imports,
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


def _resolve_response_representation(
    op: OperationConfig,
    resource: ResourceConfig,
) -> RepresentationConfig | None:
    """Pick the representation to return on a write op, if any.

    Write ops only honour the *explicit* :attr:`OperationConfig.representation`
    -- the resource's :attr:`ResourceConfig.default_representation`
    is the cross-resource default for read ops and saved-view
    hydration; inheriting it on writes would silently change every
    create/update from "201 no body" to "201 with body" for any
    resource that opts into reps.  Make the user spell it.
    """
    name = op.representation

    if name is None:
        return None

    for rep in resource.representations:
        if rep.name == name:
            return rep

    names = [r.name for r in resource.representations]
    msg = (
        f"Operation {op.name!r}: representation={name!r} "
        f"not declared on {resource.model!r} (have: {names!r})"
    )
    raise ValueError(msg)


def _representation_response_wiring(
    ctx: BuildContext[OperationConfig, ProjectConfig],
    resource: ResourceConfig,
    rep: RepresentationConfig | None,
    model: Name,
    *,
    package_prefix: str,
) -> tuple[dict[str, object], list[tuple[str, str]], str | None, str | None]:
    """Wire body context + imports for a write op's response shape.

    Returns ``(body_context, extra_imports, response_model,
    response_schema_module)``.  When *rep* is ``None`` (the op
    returns no body), all four pieces are empty/``None``.
    """
    if rep is None:
        return {}, [], None, None

    schema_name = _representation_class_name(model, rep.name)
    response_schema_module = prefix_import(
        package_prefix,
        Name.parent_path(resource.model, levels=2),
        "schemas",
        model.snake,
    )

    if rep.builder is not None:
        try:
            builder_module, builder_name_obj = Name.from_dotted(rep.builder)

        except ValueError as exc:
            msg = (
                f"Representation {rep.name!r} on {resource.model!r}: "
                f"builder must be a dotted path (got {rep.builder!r})"
            )
            raise ValueError(msg) from exc

        serialize_call = builder_name_obj.raw
        extra_imports: list[tuple[str, str]] = [
            (builder_module, builder_name_obj.raw),
        ]

    else:
        serialize_call = representation_fn_name(model, rep.name)
        ser_module = prefix_import(
            ctx.package_prefix,
            Name.parent_path(resource.model, levels=2),
            "serializers",
            model.snake,
        )
        extra_imports = [(ser_module, serialize_call)]

    body_context: dict[str, object] = {
        "serialize_response_call": serialize_call,
    }
    return body_context, extra_imports, schema_name, response_schema_module
