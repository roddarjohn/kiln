"""Get operation: GET /{pk} -- retrieve a single resource."""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations.links import (
    _representation_class_name,
    representation_fn_name,
)
from be.operations.renderers import FETCH_OR_404_IMPORT, gate_wiring
from be.operations.types import (
    FieldsOptions,
    RouteHandler,
    RouteParam,
    TestCase,
    _construct_dump,
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


@operation("get", scope="operation", dispatch_on="name")
class Get:
    """GET /{pk} -- retrieve a single resource.

    Three ways to spell the response shape, in priority order:

    1. ``operation.representation`` -- name of a
       :class:`~be.config.schema.RepresentationConfig` declared on
       the resource.  The handler returns ``{Model}{NamePascal}``
       through that representation's auto-generated serializer
       (or the user-supplied ``builder``).
    2. ``resource.default_representation`` -- same as (1) but
       inherited; used when the op didn't pick one.
    3. ``operation.fields`` -- ad-hoc per-op shape; emits a
       ``{Model}Resource`` schema and matching serializer.

    A custom :attr:`OperationConfig.serializer` overrides the
    response model regardless of which path produced the schema.
    """

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for GET /{pk}.

        Yields:
            Schema + serializer (if ad-hoc fields), the route
            handler, and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        model_module, model = Name.from_dotted(resource.model)
        include_actions = resource.include_actions_in_dump
        custom_serializer = ctx.instance.serializer

        rep = _resolve_representation(ctx.instance, resource)

        if rep is not None:
            yield from _build_with_representation(
                ctx,
                resource,
                rep,
                model,
                custom_serializer=custom_serializer,
            )
            return

        # Legacy ad-hoc path: op declares its own ``fields:``.
        _ensure_fields(ctx.instance, options)

        dump = _construct_dump(
            model,
            model_module,
            options.fields,
            suffix="Resource",
            stem="resource",
            include_actions=include_actions,
        )

        # Nested sub-schemas / sub-serializers are ordered deepest-first
        # so they render before the parent class that references them.
        yield from dump.nested_schemas
        yield dump.main_schema

        # Auto-generated serializers are skipped when the op
        # carries a custom serializer; the schema is still emitted
        # so other ops on the resource and any FE consumer of the
        # schema name keep working.
        if custom_serializer is None:
            yield from dump.nested_serializers
            yield dump.main_serializer

        gate_ctx, gate_imports = gate_wiring(
            ctx.instance,
            resource,
            ctx.package_prefix,
            is_object_scope=True,
        )

        if custom_serializer is not None:
            try:
                ser_module, ser_name_obj = Name.from_dotted(custom_serializer)

            except ValueError as exc:
                msg = (
                    f"Operation {ctx.instance.name!r}: serializer "
                    f"must be a dotted path (got "
                    f"{custom_serializer!r})"
                )
                raise ValueError(msg) from exc

            serializer_fn = ser_name_obj.raw
            serializer_fn_module = ser_module
            response_model: str | None = None
            return_type = "dict[str, Any]"

        else:
            serializer_fn = dump.main_serializer.function_name
            serializer_fn_module = None
            response_model = dump.main_schema.name
            return_type = dump.main_schema.name

        extra_imports: list[tuple[str, str]] = [
            ("sqlalchemy", "select"),
            FETCH_OR_404_IMPORT,
            *dump.load_imports,
            *gate_imports,
        ]

        if custom_serializer is not None:
            extra_imports.append(("typing", "Any"))

        yield RouteHandler(
            method="GET",
            path=f"/{{{resource.pk.name}}}",
            function_name=f"get_{model.snake}",
            op_name=ctx.instance.name,
            params=[
                RouteParam(
                    name=resource.pk.name,
                    annotation=PYTHON_TYPES[resource.pk.type],
                )
            ],
            response_model=response_model,
            serializer_fn=serializer_fn,
            serializer_fn_module=serializer_fn_module,
            return_type=return_type,
            doc=f"Get a {model.pascal} by {resource.pk.name}.",
            body_template="fastapi/ops/get.py.j2",
            body_context={
                "load_options": dump.load_options,
                "serializer_async": include_actions,
                "custom_serializer": custom_serializer is not None,
                **gate_ctx,
            },
            extra_imports=extra_imports,
        )

        yield TestCase(
            op_name="get",
            method="get",
            path=f"/{{{resource.pk.name}}}",
            status_success=200,
            status_not_found=404,
            response_schema=(
                dump.main_schema.name if custom_serializer is None else None
            ),
        )


def _resolve_representation(
    op: OperationConfig,
    resource: ResourceConfig,
) -> RepresentationConfig | None:
    """Pick the representation an op should use, if any.

    Priority: explicit ``op.representation`` -> resource's
    ``default_representation``.  Returns ``None`` when neither is
    set, in which case the caller falls back to the legacy
    ``options.fields`` path.

    Raises:
        ValueError: When ``op.representation`` is set but doesn't
            match any entry in ``resource.representations``.

    """
    explicit = op.representation

    if explicit is not None:
        for rep in resource.representations:
            if rep.name == explicit:
                return rep

        names = [r.name for r in resource.representations]
        msg = (
            f"Operation {op.name!r}: representation={explicit!r} "
            f"not declared on {resource.model!r} (have: {names!r})"
        )
        raise ValueError(msg)

    default = resource.default_representation

    if default is None:
        return None

    for rep in resource.representations:
        if rep.name == default:
            return rep

    msg = (  # pragma: no cover -- ResourceConfig validator catches this
        f"Resource {resource.model!r}: default_representation="
        f"{default!r} not in representations."
    )
    raise AssertionError(msg)


def _ensure_fields(op: OperationConfig, options: FieldsOptions) -> None:
    """Reject a read op that has no field list and no representation.

    The resource-level :attr:`ResourceConfig.default_representation`
    is one fallback; an op-level :attr:`OperationConfig.representation`
    is another; a per-op ``fields`` declaration is the third.  At
    least one must be set or the handler has nothing to return.
    """
    if options.fields:
        return

    msg = (
        f"Operation {op.name!r}: no response shape configured.  "
        f"Set `representation:`, declare a `default_representation` "
        f"on the resource, or pass an explicit `fields:` list."
    )
    raise ValueError(msg)


def _build_with_representation(
    ctx: BuildContext[OperationConfig, ProjectConfig],
    resource: ResourceConfig,
    rep: RepresentationConfig,
    model: Name,
    *,
    custom_serializer: str | None,
) -> Iterable[object]:
    """Emit the get handler + test case wired to a representation.

    The schema and (for fields-driven reps) serializer are emitted
    by the :class:`~be.operations.links.RepresentationSchemas`
    op; this branch only emits the handler and test case.
    """
    schema_name = _representation_class_name(model, rep.name)

    if custom_serializer is not None:
        try:
            ser_module, ser_name_obj = Name.from_dotted(custom_serializer)

        except ValueError as exc:
            msg = (
                f"Operation {ctx.instance.name!r}: serializer "
                f"must be a dotted path (got {custom_serializer!r})"
            )
            raise ValueError(msg) from exc

        serializer_fn = ser_name_obj.raw
        serializer_fn_module: str | None = ser_module
        response_model: str | None = None
        return_type = "dict[str, Any]"
        # Custom serializer keeps the existing ``(obj, session, db)``
        # signature even when the op points at a representation; the
        # template branch is the same.
        body_template_flags = {
            "custom_serializer": True,
            "serializer_async": False,
        }

    elif rep.builder is not None:
        try:
            builder_module, builder_name_obj = Name.from_dotted(rep.builder)

        except ValueError as exc:
            msg = (
                f"Representation {rep.name!r} on {resource.model!r}: "
                f"builder must be a dotted path (got {rep.builder!r})"
            )
            raise ValueError(msg) from exc

        serializer_fn = builder_name_obj.raw
        serializer_fn_module = builder_module
        response_model = schema_name
        return_type = schema_name
        body_template_flags = {
            "custom_serializer": False,
            "serializer_async": True,
        }

    else:
        serializer_fn = representation_fn_name(model, rep.name)
        # Auto-generated rep serializers live in the resource's
        # serializers module -- ``None`` lets the route renderer
        # resolve the import to the generated path.
        serializer_fn_module = None
        response_model = schema_name
        return_type = schema_name
        body_template_flags = {
            "custom_serializer": False,
            "serializer_async": True,
        }

    gate_ctx, gate_imports = gate_wiring(
        ctx.instance,
        resource,
        ctx.package_prefix,
        is_object_scope=True,
    )

    extra_imports: list[tuple[str, str]] = [
        ("sqlalchemy", "select"),
        FETCH_OR_404_IMPORT,
        *gate_imports,
    ]

    if custom_serializer is not None:
        extra_imports.append(("typing", "Any"))

    # Pull the rep's schema in from the resource's schemas module
    # when the route renderer can't (custom serializers and rep
    # builders both point ``response_schema_module`` at the user's
    # module otherwise).
    response_schema_module = (
        prefix_import(
            ctx.package_prefix,
            Name.parent_path(resource.model, levels=2),
            "schemas",
            model.snake,
        )
        if response_model
        else None
    )

    yield RouteHandler(
        method="GET",
        path=f"/{{{resource.pk.name}}}",
        function_name=f"get_{model.snake}",
        op_name=ctx.instance.name,
        params=[
            RouteParam(
                name=resource.pk.name,
                annotation=PYTHON_TYPES[resource.pk.type],
            )
        ],
        response_model=response_model,
        response_schema_module=response_schema_module,
        serializer_fn=serializer_fn,
        serializer_fn_module=serializer_fn_module,
        return_type=return_type,
        doc=f"Get a {model.pascal} by {resource.pk.name}.",
        body_template="fastapi/ops/get.py.j2",
        body_context={
            "load_options": [],
            **body_template_flags,
            **gate_ctx,
        },
        extra_imports=extra_imports,
    )

    yield TestCase(
        op_name="get",
        method="get",
        path=f"/{{{resource.pk.name}}}",
        status_success=200,
        status_not_found=404,
        response_schema=(schema_name if custom_serializer is None else None),
    )
