"""Shared lookup helper for list-modifier ops.

Modifier ops (Filter / Order / Paginate) run at the ``modifier``
scope, nested inside a specific list op's config.  Their parent
in the scope tree is that specific list op — no sibling search,
no ambiguity.  This helper extracts the parent list's outputs and
the enclosing resource's model name for the three modifier ops
to share.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast

from foundry.naming import Name
from kiln.operations.types import RouteHandler, SchemaClass

if TYPE_CHECKING:
    from foundry.engine import BuildContext
    from kiln.config.schema import ModifierConfig, ResourceConfig


class ListOutputs(NamedTuple):
    """The parent list's outputs a modifier op amends."""

    resource: ResourceConfig
    model: Name
    search_request: SchemaClass
    handler: RouteHandler


def find_list_outputs(ctx: BuildContext[ModifierConfig]) -> ListOutputs:
    """Locate the parent list op's SearchRequest and search handler.

    Modifier ops nest inside a specific list op's config, so the
    parent in the scope tree *is* that list op.  We walk up one
    level via :meth:`BuildStore.ancestor_id_of` and read outputs
    registered under that id.  No name-matching on siblings.

    Raises:
        LookupError: If the parent op isn't registered or didn't
            emit the expected SearchRequest / handler (would
            indicate an engine-ordering bug, not a user error).

    """
    parent_id = ctx.store.ancestor_id_of(ctx.instance_id, "operation")
    if parent_id is None:
        msg = "Modifier op has no enclosing operation."
        raise LookupError(msg)

    resource = cast(
        "ResourceConfig",
        ctx.store.ancestor_of(ctx.instance_id, "resource"),
    )
    _, model = Name.from_dotted(resource.model)

    search_request = next(
        (
            s
            for s in ctx.store.outputs_from(parent_id, "list", SchemaClass)
            if s.name == model.suffixed("SearchRequest")
        ),
        None,
    )
    if search_request is None:
        msg = (
            f"Modifier under '{resource.model}' ran before List emitted "
            f"its SearchRequest."
        )
        raise LookupError(msg)

    handler = next(
        (
            h
            for h in ctx.store.outputs_from(parent_id, "list", RouteHandler)
            if h.method == "POST" and h.path == "/search"
        ),
        None,
    )
    if handler is None:
        msg = (
            f"Modifier under '{resource.model}' ran before List emitted "
            f"its POST /search handler."
        )
        raise LookupError(msg)

    return ListOutputs(
        resource=resource,
        model=model,
        search_request=search_request,
        handler=handler,
    )
