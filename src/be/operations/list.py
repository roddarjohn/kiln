"""List operation: POST /search -- bare list endpoint.

Emits an always-present ``POST /search`` route plus the schemas
and serializer every list op needs.  Modifier ops (Filter / Order
/ Paginate) nest inside a list op's config and reach its outputs
via a :class:`ListResult` bundle that List yields alongside the
individual outputs.  The bundle carries direct references to each
emitted object, so modifiers amend fields on the bundle rather
than searching the store by name or shape.
"""

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel
from pydantic import Field as PydanticField

from be.config.schema import FieldSpec  # noqa: TC001
from be.operations._naming import collection_specs_const
from be.operations.representations import (
    RepresentationSpec,
    pick_representation,
)
from be.operations.types import (
    RouteHandler,
    RouteParam,
    SchemaClass,
    SerializerFn,
    TestCase,
    _construct_dump,
)
from foundry.naming import Name, prefix_import
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import (
        ModifierConfig,
        OperationConfig,
        ProjectConfig,
        ResourceConfig,
    )
    from be.operations.types import _DumpOutputs
    from foundry.engine import BuildContext


@dataclass
class ListResult:
    """Direct references to everything the list op emits.

    Not a rendered output (see the no-op renderer registered in
    :mod:`be.operations.renderers`) — just a typed handle that
    modifier ops fetch to amend specific objects by name rather
    than scanning the store.

    ``list_item`` and ``serializer`` are ``None`` when the list op
    points at a representation (the schema + serializer are owned
    by :class:`~be.operations.links.RepresentationSchemas` in that
    case); modifiers don't consume them.
    """

    list_item: SchemaClass | None
    serializer: SerializerFn | None
    item_type: str
    """Pydantic class name of one list row.  Used by Paginate to
    wrap the response in a ``{Model}Page``."""
    search_request: SchemaClass
    handler: RouteHandler
    test_case: TestCase


@operation("list", scope="operation", dispatch_on="name")
class List:
    """POST /search -- list resources.

    Always emits:

    * ``{Model}SearchRequest`` request schema (empty unless an
      extension op — Filter / Order / Paginate — fills it in).
    * ``POST /search`` route handler and its test case.

    Plus, when no rep applies, the ad-hoc ``{Model}ListItem``
    schema and serializer.  Extension ops run after this one
    and amend the SearchRequest + handler in place.
    """

    class Options(BaseModel):
        """Options for the list operation.

        ``fields`` is optional because the op can alternatively
        select a representation; the build method picks which path
        applies.
        """

        fields: list[FieldSpec] = PydanticField(default_factory=list)

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: Options,
    ) -> Iterable[object]:
        """Emit the list outputs."""
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        model_module, model = Name.from_dotted(resource.model)
        custom_serializer = ctx.instance.serializer
        spec = pick_representation(ctx)

        search_request = SchemaClass(
            name=model.suffixed("SearchRequest"),
            body_template="fastapi/schema_parts/search_request.py.j2",
            body_context={
                "model_name": model.pascal,
                "has_filter": False,
                "has_sort": False,
                "pagination_mode": None,
                "default_page_size": 20,
            },
        )

        if spec is None:
            if not options.fields:
                msg = (
                    f"Operation {ctx.instance.name!r}: no response "
                    f"shape configured.  Set `representation:`, "
                    f"declare a `default_representation` on the "
                    f"resource, or pass an explicit `fields:` list."
                )
                raise ValueError(msg)

            dump = _construct_dump(
                model,
                model_module,
                options.fields,
                suffix="ListItem",
                stem="list_item",
                include_actions=resource.include_actions_in_dump,
            )

        else:
            dump = None

        wiring = _ListWiring.resolve(
            spec=spec,
            dump=dump,
            include_actions=resource.include_actions_in_dump,
            custom_serializer=custom_serializer,
            op_name=ctx.instance.name,
        )

        body_context: dict[str, object] = {
            "has_filter": False,
            "has_sort": False,
            "pagination_mode": None,
            "default_sort_field": resource.pk.name,
            "default_sort_dir": "asc",
            "max_page_size": 100,
            "cursor_field": resource.pk.name,
            "load_options": wiring.load_options,
            "serializer_async": wiring.serializer_async,
            "custom_serializer": wiring.is_custom,
            "include_actions": wiring.include_actions,
        }

        extra_imports: list[tuple[str, str]] = [
            ("sqlalchemy", "select"),
            *wiring.load_imports,
        ]

        if wiring.is_custom:
            extra_imports.append(("typing", "Any"))

        if wiring.include_actions:
            actions_module = prefix_import(
                ctx.package_prefix,
                Name.parent_path(resource.model, levels=2),
                "actions",
            )
            collection_const = collection_specs_const(model)
            extra_imports.extend(
                [
                    ("ingot.actions", "filter_visible"),
                    ("ingot.actions", "find_can"),
                    (actions_module, collection_const),
                ]
            )
            body_context["collection_specs_const"] = collection_const

        handler = RouteHandler(
            method="POST",
            path="/search",
            function_name=f"list_{model.snake}s",
            op_name=ctx.instance.name,
            params=[
                RouteParam(name="body", annotation=search_request.name),
            ],
            response_model=wiring.response_model,
            response_schema_module=wiring.response_schema_module,
            return_type=wiring.return_type,
            serializer_fn=wiring.serializer_fn,
            serializer_fn_module=wiring.serializer_fn_module,
            request_schema=search_request.name,
            doc=f"List {model.pascal} records.",
            body_template="fastapi/ops/search.py.j2",
            body_context=body_context,
            extra_imports=extra_imports,
        )

        test_case = TestCase(
            op_name="list",
            method="post",
            path="/search",
            status_success=200,
            has_request_body=True,
            request_schema=search_request.name,
            is_list_response=True,
        )

        # Ad-hoc-fields path emits the per-op schema + serializer
        # alongside the always-present search machinery.  Nested
        # sub-schemas / sub-serializers come deepest-first so they
        # render before the parent.
        if dump is not None:
            yield from dump.nested_schemas
            yield dump.main_schema

            if custom_serializer is None:
                yield from dump.nested_serializers
                yield dump.main_serializer

        yield search_request
        yield handler
        yield test_case
        yield ListResult(
            list_item=dump.main_schema if dump is not None else None,
            serializer=dump.main_serializer if dump is not None else None,
            item_type=wiring.item_type,
            search_request=search_request,
            handler=handler,
            test_case=test_case,
        )


# -------------------------------------------------------------------
# Wiring
# -------------------------------------------------------------------


@dataclass
class _ListWiring:
    """Resolved response wiring for the list handler."""

    response_model: str | None
    response_schema_module: str | None
    return_type: str
    serializer_fn: str
    serializer_fn_module: str | None
    item_type: str
    """Pydantic class name of one row -- carried into
    :class:`ListResult` so :class:`~be.operations.paginate.Paginate`
    can wrap it in a ``{Model}Page``."""
    load_options: list[str] = dc_field(default_factory=list)
    load_imports: list[tuple[str, str]] = dc_field(default_factory=list)
    serializer_async: bool = False
    include_actions: bool = False
    is_custom: bool = False

    @classmethod
    def resolve(
        cls,
        *,
        spec: RepresentationSpec | None,
        dump: _DumpOutputs | None,
        include_actions: bool,
        custom_serializer: str | None,
        op_name: str,
    ) -> _ListWiring:
        """Pick the wiring shape from the inputs."""
        if spec is not None:
            wiring = cls(
                response_model=f"list[{spec.schema_class}]",
                response_schema_module=spec.schema_module,
                return_type=f"list[{spec.schema_class}]",
                serializer_fn=spec.serializer_fn,
                serializer_fn_module=spec.serializer_fn_module,
                item_type=spec.schema_class,
                serializer_async=True,
            )

        else:
            assert dump is not None  # noqa: S101 -- caller invariant
            wiring = cls(
                response_model=f"list[{dump.main_schema.name}]",
                response_schema_module=None,
                return_type=f"list[{dump.main_schema.name}]",
                serializer_fn=dump.main_serializer.function_name,
                serializer_fn_module=None,
                item_type=dump.main_schema.name,
                load_options=dump.load_options,
                load_imports=list(dump.load_imports),
                serializer_async=include_actions,
                include_actions=include_actions,
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

        # Custom serializer drops ``response_model`` so FastAPI
        # doesn't validate against the auto schema; the function
        # is responsible for the dict shape it returns.
        return _ListWiring(
            response_model=None,
            response_schema_module=None,
            return_type="list[dict[str, Any]]",
            serializer_fn=ser_name_obj.raw,
            serializer_fn_module=ser_module,
            item_type=wiring.item_type,
            load_options=wiring.load_options,
            load_imports=wiring.load_imports,
            serializer_async=False,
            include_actions=False,
            is_custom=True,
        )


# -------------------------------------------------------------------
# Modifier-op lookup
#
# Filter / Order / Paginate nest as children of a specific list op
# in the scope tree.  Each fetches the parent list's
# :class:`ListResult` bundle via
# ``ctx.store.output_under_ancestor(ctx.instance_id, "operation",
# ListResult)`` and amends fields directly — no store scanning, no
# name/shape matching.  :func:`resource_model` here spares each
# modifier from re-deriving the model Name it needs for schema
# naming.
# -------------------------------------------------------------------


def resource_model(ctx: BuildContext[ModifierConfig, ProjectConfig]) -> Name:
    """Return the model :class:`~foundry.naming.Name` of the resource."""
    resource = cast(
        "ResourceConfig",
        ctx.store.ancestor_of(ctx.instance_id, "resource"),
    )

    _, model = Name.from_dotted(resource.model)

    return model
