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

from be.config.schema import FieldSpec  # noqa: TC001
from be.operations._naming import (
    app_module_for,
    collection_specs_const,
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
    from foundry.engine import BuildContext


@dataclass
class ListResult:
    """Direct references to everything the list op emits.

    Not a rendered output (see the no-op renderer registered in
    :mod:`be.operations.renderers`) — just a typed handle that
    modifier ops fetch to amend specific objects by name rather
    than scanning the store.
    """

    list_item: SchemaClass
    serializer: SerializerFn
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
        """Options for the list operation."""

        fields: list[FieldSpec]

    def build(
        self,
        ctx: BuildContext[OperationConfig, ProjectConfig],
        options: Options,
    ) -> Iterable[object]:
        """Emit the list schemas, serializer, handler, and test case.

        Args:
            ctx: Build context for the ``"list"`` op entry.
            options: Parsed ``Options`` (just the field list).

        Yields:
            ListItem schema, serializer, SearchRequest schema,
            search RouteHandler, and TestCase.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        model_module, model = Name.from_dotted(resource.model)
        pk_name = getattr(resource, "pk", "id")
        include_actions = resource.include_actions_in_dump
        custom_serializer = ctx.instance.serializer

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

        if custom_serializer is not None:
            ser_module, _, ser_name = custom_serializer.rpartition(".")

            if not ser_module or not ser_name:
                msg = (
                    f"Operation {ctx.instance.name!r}: serializer "
                    f"must be a dotted path (got "
                    f"{custom_serializer!r})"
                )
                raise ValueError(msg)

            serializer_fn = ser_name
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
                app_module_for(resource.model),
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
            function_name=f"list_{model.lower}s",
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
