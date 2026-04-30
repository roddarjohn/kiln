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
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel
from pydantic import Field as PydanticField

from be.config.schema import FieldSpec  # noqa: TC001
from be.operations._naming import (
    collection_specs_const,
)
from be.operations.links import (
    _representation_class_name,
    representation_fn_name,
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
        RepresentationConfig,
        ResourceConfig,
    )
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
    """Pydantic class name of one list row -- e.g.
    ``"ProductListItem"`` (legacy ad-hoc fields) or
    ``"ProductDefault"`` (representation).  Used by the Paginate
    modifier to wrap the response in a ``{Model}Page``."""
    search_request: SchemaClass
    handler: RouteHandler
    test_case: TestCase


@operation("list", scope="operation", dispatch_on="name")
class List:
    """POST /search -- list resources.

    Always emits:

    * ``{Model}ListItem`` response schema + matching serializer.
    * ``{Model}SearchRequest`` request schema (empty unless an
      extension op — Filter / Order / Paginate — fills it in).
    * ``POST /search`` route handler and its test case.

    Extension ops run after this one (they declare
    ``requires=["list"]``) and amend the SearchRequest + handler
    in place.
    """

    class Options(BaseModel):
        """Options for the list operation.

        ``fields`` is optional because the op can alternatively
        select a representation (via ``OperationConfig.representation``
        or the resource's ``default_representation``); the build
        method resolves which path applies.
        """

        fields: list[FieldSpec] = PydanticField(default_factory=list)

    def build(  # noqa: PLR0915
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: Options,
    ) -> Iterable[object]:
        """Emit the list schemas, serializer, handler, and test case.

        Yields:
            For the legacy ad-hoc-fields path: ListItem schema,
            its serializer, SearchRequest, route handler, test
            case, and a :class:`ListResult`.

            For the representation path: SearchRequest, route
            handler, test case, and a :class:`ListResult` (the
            schema + serializer come from
            :class:`~be.operations.links.RepresentationSchemas`).

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        model_module, model = Name.from_dotted(resource.model)
        pk_name = resource.pk.name
        include_actions = resource.include_actions_in_dump
        custom_serializer = ctx.instance.serializer

        rep = _resolve_representation(ctx.instance, resource)

        search_request_name = model.suffixed("SearchRequest")
        search_request = SchemaClass(
            name=search_request_name,
            body_template="fastapi/schema_parts/search_request.py.j2",
            body_context={
                "model_name": model.pascal,
                "has_filter": False,
                "has_sort": False,
                "pagination_mode": None,
                "default_page_size": 20,
            },
        )

        if rep is not None:
            yield from _build_with_representation(
                ctx,
                resource,
                rep,
                model,
                pk_name=pk_name,
                search_request=search_request,
                custom_serializer=custom_serializer,
            )
            return

        if not options.fields:
            msg = (
                f"Operation {ctx.instance.name!r}: no response shape "
                f"configured.  Set `representation:`, declare a "
                f"`default_representation` on the resource, or pass "
                f"an explicit `fields:` list."
            )
            raise ValueError(msg)

        dump = _construct_dump(
            model,
            model_module,
            options.fields,
            suffix="ListItem",
            stem="list_item",
            include_actions=include_actions,
        )
        list_item = dump.main_schema
        serializer = dump.main_serializer

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
            serializer_fn_module: str | None = ser_module
            response_model: str | None = None
            return_type = "list[dict[str, Any]]"

        else:
            serializer_fn = serializer.function_name
            serializer_fn_module = None
            response_model = f"list[{list_item.name}]"
            return_type = response_model

        body_context: dict[str, object] = {
            "has_filter": False,
            "has_sort": False,
            "pagination_mode": None,
            "default_sort_field": pk_name,
            "default_sort_dir": "asc",
            "max_page_size": 100,
            "cursor_field": pk_name,
            "load_options": dump.load_options,
            "serializer_async": include_actions,
            "custom_serializer": custom_serializer is not None,
            "include_actions": include_actions,
        }

        extra_imports: list[tuple[str, str]] = [
            ("sqlalchemy", "select"),
            *dump.load_imports,
        ]

        if custom_serializer is not None:
            extra_imports.append(("typing", "Any"))

        if include_actions:
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
                RouteParam(name="body", annotation=search_request_name),
            ],
            response_model=response_model,
            return_type=return_type,
            serializer_fn=serializer_fn,
            serializer_fn_module=serializer_fn_module,
            request_schema=search_request_name,
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
            request_schema=search_request_name,
            is_list_response=True,
        )

        # Nested sub-schemas / sub-serializers are ordered deepest-first
        # so they render before the parent class that references them.
        yield from dump.nested_schemas
        yield list_item

        if custom_serializer is None:
            yield from dump.nested_serializers
            yield serializer

        yield search_request
        yield handler
        yield test_case
        yield ListResult(
            list_item=list_item,
            serializer=serializer,
            item_type=list_item.name,
            search_request=search_request,
            handler=handler,
            test_case=test_case,
        )


def _resolve_representation(
    op: OperationConfig,
    resource: ResourceConfig,
) -> RepresentationConfig | None:
    """Pick the representation a list/get op should use, if any.

    Mirrors :func:`be.operations.get._resolve_representation`;
    duplicated to avoid a cross-module import cycle (``get``
    imports ``list`` via :class:`ListResult` rendering).
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


def _build_with_representation(  # noqa: PLR0913
    ctx: BuildContext[OperationConfig, ProjectConfig],
    resource: ResourceConfig,
    rep: RepresentationConfig,
    model: Name,
    *,
    pk_name: str,
    search_request: SchemaClass,
    custom_serializer: str | None,
) -> Iterable[object]:
    """List handler + test + ListResult wired to a representation."""
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
        return_type = "list[dict[str, Any]]"
        serializer_async = False

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
        response_model = f"list[{schema_name}]"
        return_type = response_model
        serializer_async = True

    else:
        serializer_fn = representation_fn_name(model, rep.name)
        serializer_fn_module = None
        response_model = f"list[{schema_name}]"
        return_type = response_model
        serializer_async = True

    body_context: dict[str, object] = {
        "has_filter": False,
        "has_sort": False,
        "pagination_mode": None,
        "default_sort_field": pk_name,
        "default_sort_dir": "asc",
        "max_page_size": 100,
        "cursor_field": pk_name,
        "load_options": [],
        "serializer_async": serializer_async,
        "custom_serializer": custom_serializer is not None,
        "include_actions": False,
    }

    extra_imports: list[tuple[str, str]] = [("sqlalchemy", "select")]

    if custom_serializer is not None:
        extra_imports.append(("typing", "Any"))

    response_schema_module = (
        prefix_import(
            ctx.package_prefix,
            Name.parent_path(resource.model, levels=2),
            "schemas",
            model.snake,
        )
        if response_model and not response_model.startswith("list[dict")
        else None
    )

    handler = RouteHandler(
        method="POST",
        path="/search",
        function_name=f"list_{model.snake}s",
        op_name=ctx.instance.name,
        params=[
            RouteParam(name="body", annotation=search_request.name),
        ],
        response_model=response_model,
        response_schema_module=response_schema_module,
        return_type=return_type,
        serializer_fn=serializer_fn,
        serializer_fn_module=serializer_fn_module,
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

    yield search_request
    yield handler
    yield test_case
    yield ListResult(
        list_item=None,
        serializer=None,
        item_type=schema_name,
        search_request=search_request,
        handler=handler,
        test_case=test_case,
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
