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
from be.operations.types import Field, SchemaClass, SerializerFn
from foundry.imports import ImportCollector
from foundry.naming import Name, prefix_import
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import (
        App,
        ProjectConfig,
        RepresentationConfig,
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
            class_name = _representation_class_name(model, rep.name)
            rep_fields = [] if rep.builder is not None else rep.fields

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


def _representation_class_name(model: Name, rep_name: str) -> str:
    """Compute the Pydantic class name for one representation.

    ``Article`` + ``"default"`` -> ``"ArticleDefault"``;
    ``Article`` + ``"detail_view"`` -> ``"ArticleDetailView"``.
    """
    return f"{model.pascal}{Name(rep_name).pascal}"


def representation_fn_name(model: Name, rep_name: str) -> str:
    """Compute the auto-generated serializer name for one representation.

    ``Article`` + ``"default"`` -> ``"to_article_default"``;
    ``Article`` + ``"detail_view"`` -> ``"to_article_detail_view"``.
    """
    return f"to_{model.snake}_{Name(rep_name).snake}"


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

    rep = _find_representation(resource, rep_name)

    model_module, model_name = Name.from_dotted(resource.model)
    slug = model_name.snake
    rep_class = _representation_class_name(model_name, rep.name)
    schema_module = prefix_import(
        package_prefix,
        Name.parent_path(resource.model, levels=2),
        "schemas",
        slug,
    )
    imports.add_from(schema_module, rep_class)

    # Always import the model — the ref resolver fetches rows by
    # id even when the builder is user-supplied.
    imports.add_from(model_module, model_name.pascal)
    pk_attr = resource.pk.name
    resolver_fn_name = f"_resolve_{slug}_refs"

    if rep.builder is not None:
        try:
            builder_module, builder_name_obj = Name.from_dotted(rep.builder)

        except ValueError as exc:
            msg = (
                f"representation builder for {resource.model!r} "
                f"({rep.name!r}) must be a dotted path "
                f"(got {rep.builder!r})"
            )
            raise ValueError(msg) from exc

        builder_name = builder_name_obj.raw
        imports.add_from(builder_module, builder_name)
        return {
            "slug": slug,
            "fn_name": builder_name,
            "is_user_builder": True,
            "model_class": model_name.pascal,
            "rep_class": rep_class,
            "pk_attr": pk_attr,
            "resolver_fn_name": resolver_fn_name,
        }

    fn_name = f"_link_{slug}"

    return {
        "slug": slug,
        "fn_name": fn_name,
        "is_user_builder": False,
        "model_class": model_name.pascal,
        "rep_class": rep_class,
        "fields": [{"name": f.name} for f in rep.fields],
        "pk_attr": pk_attr,
        "resolver_fn_name": resolver_fn_name,
    }


def _find_representation(
    resource: ResourceConfig, name: str
) -> RepresentationConfig:
    """Locate a representation by name on *resource*.

    The resource-level validator already checked the name resolves,
    so a missing entry here is a programmer error.
    """
    for rep in resource.representations:
        if rep.name == name:
            return rep

    msg = (
        f"representation {name!r} not declared on {resource.model!r} "
        "(should have been caught by ResourceConfig validator)"
    )
    raise AssertionError(msg)
