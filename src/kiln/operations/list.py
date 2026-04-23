"""List operation: POST /search -- bare list endpoint.

Emits an always-present ``POST /search`` route plus the schemas
and serializer that any list op needs.  Modifier ops (Filter /
Order / Paginate) nest inside a list op's config and find its
outputs via :func:`find_search_request` and
:func:`find_search_handler` — the authoritative "where are my
outputs" contract lives here, next to the op that produces them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from kiln.config.schema import FieldSpec  # noqa: TC001
from kiln.operations.types import (
    RouteHandler,
    RouteParam,
    SchemaClass,
    TestCase,
    _construct_response_schema,
    _construct_serializer,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import (
        ModifierConfig,
        OperationConfig,
        ResourceConfig,
    )


@operation("list", scope="operation", dispatch_on="name", requires=["get"])
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
        ctx: BuildContext[OperationConfig],
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
        _, model = Name.from_dotted(resource.model)
        pk_name = getattr(resource, "pk", "id")

        list_item_schema = _construct_response_schema(
            model, options.fields, suffix="ListItem"
        )
        serializer = _construct_serializer(
            model, list_item_schema, stem="list_item"
        )
        yield list_item_schema
        yield serializer

        search_request_name = model.suffixed("SearchRequest")
        yield SchemaClass(
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

        response_model = f"list[{list_item_schema.name}]"
        yield RouteHandler(
            method="POST",
            path="/search",
            function_name=f"list_{model.lower}s",
            params=[
                RouteParam(name="body", annotation=search_request_name),
            ],
            response_model=response_model,
            return_type=response_model,
            serializer_fn=serializer.function_name,
            request_schema=search_request_name,
            doc=f"List {model.pascal} records.",
            body_template="fastapi/ops/search.py.j2",
            body_context={
                "has_filter": False,
                "has_sort": False,
                "pagination_mode": None,
                "default_sort_field": pk_name,
                "default_sort_dir": "asc",
                "max_page_size": 100,
                "cursor_field": pk_name,
            },
            extra_imports=[("sqlalchemy", "select")],
        )

        yield TestCase(
            op_name="list",
            method="post",
            path="/search",
            status_success=200,
            has_request_body=True,
            request_schema=search_request_name,
            is_list_response=True,
        )


# -------------------------------------------------------------------
# Modifier-op lookup helpers
#
# Filter / Order / Paginate nest as children of a specific list op
# in the scope tree.  To amend that list's outputs, a modifier asks
# the helpers below for the SearchRequest schema and the search
# handler registered by its parent list.  Keeping these here —
# rather than in a sidecar module — makes the list op the sole
# source of truth for its own output shape.
# -------------------------------------------------------------------


def _parent_list_id(ctx: BuildContext[ModifierConfig]) -> str:
    """Return the instance id of the list op enclosing a modifier."""
    parent_id = ctx.store.ancestor_id_of(ctx.instance_id, "operation")
    if parent_id is None:
        msg = "Modifier op has no enclosing operation."
        raise LookupError(msg)
    return parent_id


def resource_model(ctx: BuildContext[ModifierConfig]) -> Name:
    """Return the model :class:`Name` for the modifier's resource."""
    resource = cast(
        "ResourceConfig",
        ctx.store.ancestor_of(ctx.instance_id, "resource"),
    )
    _, model = Name.from_dotted(resource.model)
    return model


def find_search_request(ctx: BuildContext[ModifierConfig]) -> SchemaClass:
    """Return the parent list op's ``{Model}SearchRequest`` schema."""
    model = resource_model(ctx)
    expected = model.suffixed("SearchRequest")
    parent_id = _parent_list_id(ctx)
    match = next(
        (
            s
            for s in ctx.store.outputs_from(parent_id, "list", SchemaClass)
            if s.name == expected
        ),
        None,
    )
    if match is None:
        msg = (
            f"Modifier ran before List emitted '{expected}' — "
            f"check scope descent / engine ordering."
        )
        raise LookupError(msg)
    return match


def find_search_handler(ctx: BuildContext[ModifierConfig]) -> RouteHandler:
    """Return the parent list op's ``POST /search`` handler."""
    parent_id = _parent_list_id(ctx)
    match = next(
        (
            h
            for h in ctx.store.outputs_from(parent_id, "list", RouteHandler)
            if h.method == "POST" and h.path == "/search"
        ),
        None,
    )
    if match is None:
        msg = (
            "Modifier ran before List emitted its POST /search handler "
            "— check scope descent / engine ordering."
        )
        raise LookupError(msg)
    return match


def find_list_test_case(ctx: BuildContext[ModifierConfig]) -> TestCase | None:
    """Return the parent list op's TestCase, if tests are configured."""
    parent_id = _parent_list_id(ctx)
    return next(
        iter(ctx.store.outputs_from(parent_id, "list", TestCase)),
        None,
    )
