"""Shared lookup helpers for list-extension ops.

The Filter / Order / Paginate ops each need to locate the
``SchemaClass`` and ``RouteHandler`` that the List op emitted
under the same resource, then mutate them in place.  This module
centralizes that lookup so each extension op stays focused on its
own field contributions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast

from foundry.naming import Name
from kiln.operations.types import RouteHandler, SchemaClass

if TYPE_CHECKING:
    from foundry.engine import BuildContext
    from kiln.config.schema import OperationConfig, ResourceConfig


class ListOutputs(NamedTuple):
    """The per-resource outputs an extension op amends."""

    resource: ResourceConfig
    model: Name
    search_request: SchemaClass
    handler: RouteHandler


def find_list_outputs(ctx: BuildContext[OperationConfig]) -> ListOutputs:
    """Locate List's SearchRequest and search RouteHandler.

    Looks them up under the enclosing resource — there's exactly
    one list op per resource, so exactly one SearchRequest and one
    ``POST /search`` handler to find.

    Raises:
        LookupError: If List hasn't run yet (should be impossible
            with ``requires=["list"]``) or its outputs are missing.

    """
    resource = cast(
        "ResourceConfig",
        ctx.store.ancestor_of(ctx.instance_id, "resource"),
    )
    resource_id = ctx.store.ancestor_id_of(ctx.instance_id, "resource")

    if resource_id is None:
        msg = "Extension op has no enclosing resource."
        raise LookupError(msg)

    _, model = Name.from_dotted(resource.model)
    search_request_name = model.suffixed("SearchRequest")

    search_request = next(
        (
            s
            for s in ctx.store.outputs_under(resource_id, SchemaClass)
            if s.name == search_request_name
        ),
        None,
    )

    if search_request is None:
        msg = (
            f"Extension op for resource '{resource.model}' ran "
            f"before List emitted '{search_request_name}'."
        )
        raise LookupError(msg)

    handler = next(
        (
            h
            for h in ctx.store.outputs_under(resource_id, RouteHandler)
            if h.method == "POST" and h.path == "/search"
        ),
        None,
    )

    if handler is None:
        msg = (
            f"Extension op for resource '{resource.model}' ran "
            f"before List emitted its POST /search handler."
        )
        raise LookupError(msg)

    return ListOutputs(
        resource=resource,
        model=model,
        search_request=search_request,
        handler=handler,
    )
