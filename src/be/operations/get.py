"""Get operation: GET /{pk} -- retrieve a single resource."""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations.renderers import FETCH_OR_404_IMPORT, gate_wiring
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
    from foundry.engine import BuildContext


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
            options: Parsed :class:`~be.operations.types.FieldsOptions`.

        Yields:
            The ``{Model}Resource`` schema, its serializer, the
            route handler, and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        model_module, model = Name.from_dotted(resource.model)
        include_actions = resource.include_actions_in_dump
        custom_serializer = ctx.instance.serializer

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
            path=f"/{{{resource.pk}}}",
            function_name=f"get_{model.lower}",
            op_name=ctx.instance.name,
            params=[
                RouteParam(
                    name=resource.pk,
                    annotation=PYTHON_TYPES[resource.pk_type],
                )
            ],
            response_model=response_model,
            serializer_fn=serializer_fn,
            serializer_fn_module=serializer_fn_module,
            return_type=return_type,
            doc=f"Get a {model.pascal} by {resource.pk}.",
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
            path=f"/{{{resource.pk}}}",
            status_success=200,
            status_not_found=404,
            response_schema=(
                dump.main_schema.name if custom_serializer is None else None
            ),
        )
