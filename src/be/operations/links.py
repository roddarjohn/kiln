"""Link-related codegen for resources with a ``link:`` config.

Two operations live here:

* :class:`LinkSchema` (resource scope) — emits ``{Model}Link``
  into the resource's schemas file so cross-resource references
  (search results, ``ref`` / ``self`` filter values, saved-view
  items) get a typed Pydantic class with a ``Literal`` ``type``
  discriminator.  Without this each link would collapse to the
  same ``{type, id, name}`` dict in OpenAPI and the FE couldn't
  narrow on the resource type.

* :class:`Links` (app scope, after-children) — emits
  ``{app_module}/links.py`` with two registries keyed by slug:

  * ``LINKS`` — async ``(instance, session) -> {Model}Link``
    builders.  Generated lambdas for shorthand entries pull
    ``id`` / ``name`` straight off the model; user-supplied
    ``link.builder`` dotted paths get imported as-is.
  * ``REF_RESOLVERS`` — async ``(ids, db, session) -> (items,
    dropped)`` resolvers that fetch rows by id and run them
    through the matching link builder.  Powers saved-view
    hydration via :func:`ingot.saved_views.hydrate_view`.
"""

from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations._naming import app_module_for
from be.operations.types import SchemaClass
from foundry.imports import ImportCollector
from foundry.naming import Name, prefix_import
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import App, ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("link_schema", scope="resource")
class LinkSchema:
    """Emit ``{Model}Link`` for resources with a link config."""

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run only when the resource declares a link config."""
        return ctx.instance.link is not None

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Yield one :class:`SchemaClass` for the resource's link.

        The shape varies with :attr:`LinkConfig.kind`: ``name``
        carries a single ``name`` field, ``id`` carries an ``id``
        typed against ``pk_type``, and ``id_name`` carries both.
        ``type`` is a ``Literal[<slug>]`` so the FE-side OpenAPI
        client gets a discriminator narrowing.
        """
        resource = ctx.instance
        link = resource.link

        if link is None:  # pragma: no cover -- when() filters this
            msg = "link_schema op fired without link config"
            raise AssertionError(msg)

        _, model = Name.from_dotted(resource.model)

        yield SchemaClass(
            name=f"{model.pascal}Link",
            body_template="fastapi/schema_parts/link.py.j2",
            body_context={
                "schema_name": f"{model.pascal}Link",
                "slug": model.lower,
                "kind": link.kind,
                "id_py_type": PYTHON_TYPES[resource.pk_type],
            },
            extra_imports=[("typing", "Literal")],
        )


@operation("links", scope="app", after_children=True)
class Links:
    """Generate ``{app_module}/links.py`` with the per-app registries.

    Runs in the post-children phase of the app scope so every
    resource's :class:`~be.config.schema.LinkConfig` is fully
    visited before the registries are rendered.  Resources without
    a link config are silently skipped.
    """

    def build(
        self,
        ctx: BuildContext[App, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the app's link-registry module, when needed.

        Args:
            ctx: Build context for one
                :class:`~be.config.schema.App`.
            _options: Unused.

        Yields:
            A single :class:`~foundry.outputs.StaticFile` for
            ``{module}/links.py`` when at least one resource in
            this app declares a link; nothing otherwise.

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

            if resource.link is None:
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
    link = resource.link

    if link is None:  # pragma: no cover -- caller filters
        msg = "Resource has no link config"
        raise AssertionError(msg)

    model_module, model_name = Name.from_dotted(resource.model)
    slug = model_name.lower
    link_schema_class = f"{model_name.pascal}Link"
    schema_module = prefix_import(
        package_prefix,
        app_module_for(resource.model),
        "schemas",
        slug,
    )
    imports.add_from(schema_module, link_schema_class)

    # Always import the model — the ref resolver fetches rows by
    # id even when the link itself is built by a user function.
    imports.add_from(model_module, model_name.pascal)
    pk_attr = resource.pk
    resolver_fn_name = f"_resolve_{slug}_refs"

    if link.builder is not None:
        try:
            builder_module, builder_name_obj = Name.from_dotted(link.builder)

        except ValueError as exc:
            msg = (
                f"link.builder for {resource.model!r} must be a "
                f"dotted path (got {link.builder!r})"
            )
            raise ValueError(msg) from exc

        builder_name = builder_name_obj.raw
        imports.add_from(builder_module, builder_name)
        return {
            "slug": slug,
            "fn_name": builder_name,
            "is_user_builder": True,
            "model_class": model_name.pascal,
            "pk_attr": pk_attr,
            "resolver_fn_name": resolver_fn_name,
        }

    id_attr = link.id or resource.pk
    name_attr = link.name
    fn_name = f"_link_{slug}"

    return {
        "slug": slug,
        "fn_name": fn_name,
        "is_user_builder": False,
        "model_class": model_name.pascal,
        "link_schema_class": link_schema_class,
        "id_attr": id_attr,
        "name_attr": name_attr,
        "kind": link.kind,
        "pk_attr": pk_attr,
        "resolver_fn_name": resolver_fn_name,
    }
