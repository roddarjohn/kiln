"""Get operation: GET /{pk} -- retrieve a single resource."""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations.renderers import FETCH_OR_404_IMPORT, gate_wiring
from be.operations.representations import (
    RepresentationSpec,
    pick_representation,
)
from be.operations.types import (
    FieldsOptions,
    RouteHandler,
    RouteParam,
    TestCase,
    _construct_dump,
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
    from be.operations.types import _DumpOutputs
    from foundry.engine import BuildContext


@operation("get", scope="operation", dispatch_on="name")
class Get:
    """GET /{pk} -- retrieve a single resource.

    Three ways to spell the response shape, in priority order:

    1. ``operation.representation`` -- name of a
       :class:`~be.config.schema.RepresentationConfig` declared on
       the resource.
    2. ``resource.default_representation`` -- inherited fallback.
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
            For the ad-hoc fields path: schema + serializer plus
            handler + test case.  For the representation path:
            handler + test case (the schema + serializer come from
            :class:`~be.operations.links.RepresentationSchemas`).

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        model_module, model = Name.from_dotted(resource.model)
        custom_serializer = ctx.instance.serializer
        spec = pick_representation(ctx, fall_back_to_default=True)

        if spec is None:
            dump = _build_ad_hoc_dump(
                ctx,
                resource,
                model,
                model_module,
                options,
                custom_serializer=custom_serializer,
            )
            yield from _yield_ad_hoc_outputs(
                dump, custom_serializer=custom_serializer
            )

        else:
            dump = None

        gate_ctx, gate_imports = gate_wiring(
            ctx.instance,
            resource,
            ctx.package_prefix,
            is_object_scope=True,
        )

        wiring = _GetWiring.resolve(
            spec=spec,
            dump=dump,
            include_actions=resource.include_actions_in_dump,
            custom_serializer=custom_serializer,
            op_name=ctx.instance.name,
        )

        extra_imports: list[tuple[str, str]] = [
            ("sqlalchemy", "select"),
            FETCH_OR_404_IMPORT,
            *wiring.load_imports,
            *gate_imports,
        ]

        if wiring.is_custom:
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
            response_model=wiring.response_model,
            response_schema_module=wiring.response_schema_module,
            serializer_fn=wiring.serializer_fn,
            serializer_fn_module=wiring.serializer_fn_module,
            return_type=wiring.return_type,
            doc=f"Get a {model.pascal} by {resource.pk.name}.",
            body_template="fastapi/ops/get.py.j2",
            body_context={
                "load_options": wiring.load_options,
                "serializer_async": wiring.serializer_async,
                "custom_serializer": wiring.is_custom,
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
            response_schema=wiring.test_response_schema,
        )


# -------------------------------------------------------------------
# Wiring helpers
# -------------------------------------------------------------------


from dataclasses import dataclass, field  # noqa: E402


@dataclass
class _GetWiring:
    """Resolved response wiring for the get handler.

    Built once per op via :meth:`resolve` and consumed by the
    single ``yield RouteHandler(...)`` site so neither path (rep
    vs ad-hoc fields, plain vs custom-serializer override)
    duplicates the handler emission.
    """

    response_model: str | None
    response_schema_module: str | None
    return_type: str
    serializer_fn: str
    serializer_fn_module: str | None
    load_options: list[str] = field(default_factory=list)
    load_imports: list[tuple[str, str]] = field(default_factory=list)
    serializer_async: bool = False
    is_custom: bool = False
    test_response_schema: str | None = None

    @classmethod
    def resolve(
        cls,
        *,
        spec: RepresentationSpec | None,
        dump: _DumpOutputs | None,
        include_actions: bool,
        custom_serializer: str | None,
        op_name: str,
    ) -> _GetWiring:
        """Pick the wiring shape from the inputs.

        Exactly one of *spec* / *dump* is non-``None``: the rep
        path or the ad-hoc fields path.  Custom serializers
        override the result regardless of which path produced the
        schema.
        """
        if spec is not None:
            wiring = cls(
                response_model=spec.schema_class,
                response_schema_module=spec.schema_module,
                return_type=spec.schema_class,
                serializer_fn=spec.serializer_fn,
                serializer_fn_module=spec.serializer_fn_module,
                serializer_async=True,
                test_response_schema=spec.schema_class,
            )

        else:
            assert dump is not None  # noqa: S101 -- caller invariant
            wiring = cls(
                response_model=dump.main_schema.name,
                response_schema_module=None,
                return_type=dump.main_schema.name,
                serializer_fn=dump.main_serializer.function_name,
                serializer_fn_module=None,
                load_options=dump.load_options,
                load_imports=list(dump.load_imports),
                serializer_async=include_actions,
                test_response_schema=dump.main_schema.name,
            )

        if custom_serializer is None:
            return wiring

        try:
            ser_module, ser_name_obj = Name.from_dotted(custom_serializer)

        except ValueError as exc:
            msg = (
                f"Operation {op_name!r}: serializer must be a dotted "
                f"path (got {custom_serializer!r})"
            )
            raise ValueError(msg) from exc

        return _GetWiring(
            response_model=None,
            response_schema_module=None,
            return_type="dict[str, Any]",
            serializer_fn=ser_name_obj.raw,
            serializer_fn_module=ser_module,
            load_options=wiring.load_options,
            load_imports=wiring.load_imports,
            serializer_async=False,
            is_custom=True,
            test_response_schema=None,
        )


def _build_ad_hoc_dump(  # noqa: PLR0913
    ctx: BuildContext[OperationConfig, ProjectConfig],
    resource: ResourceConfig,
    model: Name,
    model_module: str,
    options: FieldsOptions,
    *,
    custom_serializer: str | None,
) -> _DumpOutputs:
    """Run :func:`_construct_dump` for an op that uses ad-hoc fields.

    Mostly here to keep the build method's body legible -- factors
    out the field-presence guard (a get with neither a rep nor any
    fields has nothing to return) and the dump call.
    """
    del ctx, custom_serializer  # currently unused; kept for parity

    if not options.fields:
        msg = (
            f"Operation {resource.model!r}: get has no response shape "
            f"configured.  Set `representation:`, declare a "
            f"`default_representation` on the resource, or pass an "
            f"explicit `fields:` list."
        )
        raise ValueError(msg)

    return _construct_dump(
        model,
        model_module,
        options.fields,
        suffix="Resource",
        stem="resource",
        include_actions=resource.include_actions_in_dump,
    )


def _yield_ad_hoc_outputs(
    dump: _DumpOutputs,
    *,
    custom_serializer: str | None,
) -> Iterable[object]:
    """Emit the schemas + serializers for the ad-hoc fields path.

    Nested sub-schemas / sub-serializers come deepest-first so they
    render before the parent class that references them.  The
    main serializer is skipped when a custom one is configured --
    the schema is still emitted so other ops on the resource and
    any FE consumer of the schema name keep working.
    """
    yield from dump.nested_schemas
    yield dump.main_schema

    if custom_serializer is None:
        yield from dump.nested_serializers
        yield dump.main_serializer
