"""Codegen for resource representations and the per-app link registry.

Two operations live here:

* :class:`RepresentationSchemas` (resource scope) — emits one
  Pydantic class per
  :class:`~be.config.schema.RepresentationConfig` declared on a
  resource.  Each class is named ``{Model}{NamePascal}`` and
  carries a ``type: Literal[<resource_slug>]`` discriminator so
  payloads can be narrowed when collected into cross-resource
  unions (saved-view hydration, ``ref`` autocomplete).

* :class:`Links` (app scope, after-children) — emits
  ``{app_module}/links.py`` with two registries keyed by slug,
  driven by each resource's
  :attr:`~be.config.schema.ResourceConfig.default_representation`:

  * ``LINKS`` — async ``(instance, session) -> {Model}{NamePascal}``
    builders.  Generated builders for declared ``fields:`` pull
    each named attribute off the model; user-supplied
    ``builder:`` dotted paths get imported as-is.
  * ``REF_RESOLVERS`` — async ``(ids, db, session) -> (items,
    dropped)`` resolvers that fetch rows by id and run them
    through the matching builder.  Powers saved-view hydration via
    :func:`ingot.saved_views.hydrate_view`.
"""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations.representations import (
    build_representation_spec,
    representation_class_name,
    representation_fn_name,
)
from be.operations.types import Field, SchemaClass, SerializerFn
from foundry.imports import ImportCollector
from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import (
        App,
        ProjectConfig,
        ResourceConfig,
    )
    from foundry.engine import BuildContext


@operation("representation_schemas", scope="resource")
class RepresentationSchemas:
    """Emit one ``{Model}{NamePascal}`` per declared representation."""

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run only when the resource declares any representations."""
        return bool(ctx.instance.representations)

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Yield one Pydantic class (and serializer) per representation.

        Each class carries a ``type: Literal[<slug>]`` discriminator
        plus one typed field per :attr:`RepresentationConfig.fields`
        entry.  Builder-driven representations get a
        discriminator-only shell -- the user's builder fills the
        rest, and no auto-generated serializer is emitted (the
        builder is the serializer).

        Fields-driven representations also yield a
        ``to_{model_snake}_{rep_name_snake}`` serializer that ops
        importing the rep can call: ``async (obj, session) ->
        {Model}{NamePascal}``.
        """
        resource = ctx.instance
        model_module, model = Name.from_dotted(resource.model)
        slug = model.snake

        for rep in resource.representations:
            class_name = representation_class_name(model, rep.name)
            rep_fields = [] if rep.builder is not None else rep.fields

            # The schema is identical regardless of fields vs builder.
            yield SchemaClass(
                name=class_name,
                body_template="fastapi/schema_parts/representation.py.j2",
                body_context={
                    "schema_name": class_name,
                    "slug": slug,
                    "fields": [
                        {"name": f.name, "py_type": PYTHON_TYPES[f.type]}
                        for f in rep_fields
                    ],
                },
                extra_imports=[("typing", "Literal")],
            )

            # Yield a typed handle every downstream op can fetch
            # via ``be.operations.representations.pick_representation``
            # rather than re-deriving the schema/serializer naming.
            yield build_representation_spec(
                rep, resource, model, ctx.package_prefix
            )

            if rep.builder is not None:
                # User-supplied builder is its own serializer; the
                # caller (Links op, get/list) imports it directly.
                continue

            yield SerializerFn(
                function_name=representation_fn_name(model, rep.name),
                model_name=model.pascal,
                model_module=model_module,
                schema_name=class_name,
                fields=[
                    Field(name=f.name, py_type=PYTHON_TYPES[f.type])
                    for f in rep.fields
                ],
                representation=True,
            )


@operation("links", scope="app", after_children=True)
class Links:
    """Generate ``{app_module}/links.py`` for cross-resource lookups.

    Runs in the post-children phase of the app scope so every
    resource's representations are visited before the registry is
    rendered.  Only resources that declare a
    :attr:`~be.config.schema.ResourceConfig.default_representation`
    contribute -- a resource without one isn't visible to saved-view
    hydration or ``ref`` autocomplete.
    """

    def build(
        self,
        ctx: BuildContext[App, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the app's link-registry module, when needed.

        Yields:
            A single :class:`~foundry.outputs.StaticFile` for
            ``{module}/links.py`` when at least one resource in
            this app has a default representation; nothing
            otherwise.

        """
        app = ctx.instance
        module = app.config.module
        package_prefix = ctx.package_prefix

        entries: list[dict[str, object]] = []
        imports = ImportCollector()

        for _, resource_obj in ctx.store.children(
            ctx.instance_id, child_scope="resource"
        ):
            resource = cast("ResourceConfig", resource_obj)

            if resource.default_representation is None:
                continue

            entries.append(
                _build_entry(resource, package_prefix, imports),
            )

        if not entries:
            return

        yield StaticFile(
            path=f"{module}/links.py",
            template="fastapi/links.py.j2",
            context={
                "module": module,
                "imports": imports.sorted_from_imports,
                "entries": entries,
            },
        )


def _build_entry(
    resource: ResourceConfig,
    package_prefix: str,
    imports: ImportCollector,
) -> dict[str, object]:
    """Build template context for one resource's link entry.

    Adds every needed import to *imports* so the template renders
    a single sorted import block.
    """
    rep_name = resource.default_representation

    if rep_name is None:  # pragma: no cover -- caller filters
        msg = "Resource has no default_representation"
        raise AssertionError(msg)

    rep = next(r for r in resource.representations if r.name == rep_name)

    model_module, model_name = Name.from_dotted(resource.model)
    slug = model_name.snake
    spec = build_representation_spec(rep, resource, model_name, package_prefix)

    imports.add_from(spec.schema_module, spec.schema_class)
    # Always import the model — the ref resolver fetches rows by
    # id even when the builder is user-supplied.
    imports.add_from(model_module, model_name.pascal)

    pk_attr = resource.pk.name
    resolver_fn_name = f"_resolve_{slug}_refs"

    if rep.builder is not None:
        # User builder: import it under its real name and let the
        # template emit the registry entry pointing at it.
        assert spec.serializer_fn_module is not None  # noqa: S101 -- builder
        imports.add_from(spec.serializer_fn_module, spec.serializer_fn)
        return {
            "slug": slug,
            "fn_name": spec.serializer_fn,
            "is_user_builder": True,
            "model_class": model_name.pascal,
            "rep_class": spec.schema_class,
            "pk_attr": pk_attr,
            "resolver_fn_name": resolver_fn_name,
        }

    return {
        "slug": slug,
        "fn_name": f"_link_{slug}",
        "is_user_builder": False,
        "model_class": model_name.pascal,
        "rep_class": spec.schema_class,
        "fields": [{"name": f.name} for f in rep.fields],
        "pk_attr": pk_attr,
        "resolver_fn_name": resolver_fn_name,
    }
