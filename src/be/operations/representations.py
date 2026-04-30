"""Representation wiring shared across read + write ops.

The :class:`~be.operations.links.RepresentationSchemas` op runs at
resource scope and yields one :class:`RepresentationSpec` per
declared representation -- a small, fully-resolved bundle naming
the schema class, its module, and the serializer to call.  Per-op
builders (get / list / create / update) read these out of the
build store via :func:`pick_representation` instead of each
re-deriving the wiring (schema-class name, serializer fn name,
import paths, builder vs auto-generated).

The dataclass is rendered as a no-op (registered in
:mod:`be.operations.renderers`) -- it's a typed handle for
cross-op communication, not a generated artefact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from foundry.naming import Name, prefix_import

if TYPE_CHECKING:
    from be.config.schema import (
        OperationConfig,
        ProjectConfig,
        RepresentationConfig,
        ResourceConfig,
    )
    from foundry.engine import BuildContext


@dataclass
class RepresentationSpec:
    """Resolved wiring for one named representation on a resource.

    Yielded once per :class:`~be.config.schema.RepresentationConfig`
    by :class:`~be.operations.links.RepresentationSchemas`; consumed
    by per-op builders to populate :class:`~be.operations.types.RouteHandler`
    fields without each re-implementing the lookup.

    Attributes:
        rep_name: User-given identifier (matches the resource's
            ``representations[*].name`` and any ``op.representation``
            references).
        schema_class: Generated Pydantic class name -- e.g.
            ``"WidgetDefault"``.
        schema_module: Dotted module the class lives in -- e.g.
            ``"_generated.catalog.schemas.widget"``.  Set as
            :attr:`RouteHandler.response_schema_module` so the
            route renderer imports the class from here rather than
            falling back to the per-op default.
        serializer_fn: Bare callable name -- the auto-generated
            ``to_<model>_<rep>`` for fields-driven reps, or the
            user's builder for builder-driven reps.
        serializer_fn_module: Dotted module to import
            :attr:`serializer_fn` from.  ``None`` resolves to the
            generated serializers module (auto-generated reps);
            set explicitly for user builders.

    """

    rep_name: str
    schema_class: str
    schema_module: str
    serializer_fn: str
    serializer_fn_module: str | None


def representation_class_name(model: Name, rep_name: str) -> str:
    """Compute the Pydantic class name for one representation.

    ``Article`` + ``"default"`` -> ``"ArticleDefault"``;
    ``Article`` + ``"detail_view"`` -> ``"ArticleDetailView"``.
    """
    return f"{model.pascal}{Name(rep_name).pascal}"


def representation_fn_name(model: Name, rep_name: str) -> str:
    """Compute the auto-generated serializer name for one representation.

    ``Article`` + ``"default"`` -> ``"to_article_default"``.
    """
    return f"to_{model.snake}_{Name(rep_name).snake}"


def representation_schema_module(
    package_prefix: str,
    resource: ResourceConfig,
    model: Name,
) -> str:
    """Dotted module path the rep schema lands in (the resource's schemas)."""
    return prefix_import(
        package_prefix,
        Name.parent_path(resource.model, levels=2),
        "schemas",
        model.snake,
    )


def representation_serializer_module(
    package_prefix: str,
    resource: ResourceConfig,
    model: Name,
) -> str:
    """Dotted module path for the resource's auto-generated serializers."""
    return prefix_import(
        package_prefix,
        Name.parent_path(resource.model, levels=2),
        "serializers",
        model.snake,
    )


def build_representation_spec(
    rep: RepresentationConfig,
    resource: ResourceConfig,
    model: Name,
    package_prefix: str,
) -> RepresentationSpec:
    """Resolve a config entry into a fully-wired :class:`RepresentationSpec`.

    The fields-driven branch points at the auto-generated
    serializer that :class:`~be.operations.links.RepresentationSchemas`
    emits alongside the schema; the builder branch imports the
    user-supplied dotted callable as-is.
    """
    schema_class = representation_class_name(model, rep.name)
    schema_module = representation_schema_module(
        package_prefix, resource, model
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

        return RepresentationSpec(
            rep_name=rep.name,
            schema_class=schema_class,
            schema_module=schema_module,
            serializer_fn=builder_name_obj.raw,
            serializer_fn_module=builder_module,
        )

    return RepresentationSpec(
        rep_name=rep.name,
        schema_class=schema_class,
        schema_module=schema_module,
        serializer_fn=representation_fn_name(model, rep.name),
        # ``None`` resolves to the resource's serializers module --
        # the route renderer falls through to that default.
        serializer_fn_module=None,
    )


def pick_representation(
    ctx: BuildContext[OperationConfig, ProjectConfig],
) -> RepresentationSpec | None:
    """Pick the rep spec for the current op, fetched from the store.

    Honours only the explicit :attr:`OperationConfig.representation`;
    the resource's :attr:`ResourceConfig.default_representation`
    is reserved for cross-resource surfaces (saved-view
    hydration, ``ref`` autocomplete, ``self`` filter values) and
    never silently overrides a per-op response shape.  Per-op ops
    pick a rep by name or fall back to their ad-hoc ``fields:``
    (read ops) or no-body 201/200 (write ops).

    Returns ``None`` when the op didn't pick one.

    Raises:
        ValueError: When ``op.representation`` is set but doesn't
            match any spec yielded by ``RepresentationSchemas``.

    """
    op = ctx.instance
    explicit = op.representation

    if explicit is None:
        return None

    resource = cast(
        "ResourceConfig",
        ctx.store.ancestor_of(ctx.instance_id, "resource"),
    )
    specs = _specs_by_name(ctx, resource_id=_resource_id(ctx))
    spec = specs.get(explicit)

    if spec is None:
        msg = (
            f"Operation {op.name!r}: representation={explicit!r} "
            f"not declared on {resource.model!r} "
            f"(have: {sorted(specs)!r})"
        )
        raise ValueError(msg)

    return spec


def _resource_id(ctx: BuildContext[OperationConfig, ProjectConfig]) -> str:
    """Locate the current op's enclosing resource scope id.

    The build store doesn't expose ``ancestor_id_of`` directly --
    walk the parent chain until the scope name is ``"resource"``.
    """
    current = ctx.instance_id

    while True:
        if ctx.store.scope_of(current).name == "resource":
            return current

        head, sep, _ = current.rpartition(".")

        if not sep:  # pragma: no cover -- engine guarantees the chain
            msg = f"no resource ancestor for {ctx.instance_id!r}"
            raise AssertionError(msg)

        current = head


def _specs_by_name(
    ctx: BuildContext[OperationConfig, ProjectConfig],
    *,
    resource_id: str,
) -> dict[str, RepresentationSpec]:
    """Index every :class:`RepresentationSpec` under *resource_id* by name."""
    return {
        spec.rep_name: spec
        for spec in ctx.store.outputs_under(resource_id, RepresentationSpec)
    }
