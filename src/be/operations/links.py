"""App-scope op that emits the per-app link registry module.

Walks each :class:`~be.config.schema.ResourceConfig` in an app
that declares a :class:`~be.config.schema.LinkConfig` and emits
``{app_module}/links.py`` containing two dicts keyed by slug:

* ``LINKS`` — async ``(instance, session) -> {Model}Link`` builders
  (one per resource).  Generated lambdas for shorthand entries
  pull ``id`` / ``name`` straight off the model; user-supplied
  ``link.builder`` dotted paths get imported as-is.
* ``REF_RESOLVERS`` — async ``(ids, db, session) -> (items,
  dropped)`` resolvers that fetch rows by id and run them through
  the matching link builder.  Powers saved-view hydration.

Each resource's link schema is generated separately by
:class:`~be.operations.link_schema.LinkSchema` so the typed
``{Model}Link`` Pydantic class lives in the resource's schemas
file and the OpenAPI surface gets a proper discriminated type.
"""

from typing import TYPE_CHECKING, cast

from be.operations._naming import app_module_for
from foundry.naming import Name, prefix_import
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import App, ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("links", scope="app", after_children=True)
class Links:
    """Generate ``{app_module}/links.py`` with per-resource builders.

    Runs in the post-children phase of the app scope so every
    resource's :class:`~be.config.schema.LinkConfig` is fully
    visited before the registry is rendered.  Resources without a
    link config are silently skipped.
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
        model_imports: dict[str, set[str]] = {}
        link_schema_imports: dict[str, set[str]] = {}
        builder_imports: dict[str, set[str]] = {}

        for _, resource_obj in ctx.store.children(
            ctx.instance_id, child_scope="resource"
        ):
            resource = cast("ResourceConfig", resource_obj)

            if resource.link is None:
                continue

            entry = _build_entry(
                resource,
                package_prefix,
                model_imports,
                link_schema_imports,
                builder_imports,
            )
            entries.append(entry)

        if not entries:
            return

        yield StaticFile(
            path=f"{module}/links.py",
            template="fastapi/links.py.j2",
            context={
                "module": module,
                "model_imports": _sorted_imports(model_imports),
                "link_schema_imports": _sorted_imports(link_schema_imports),
                "builder_imports": _sorted_imports(builder_imports),
                "entries": entries,
            },
        )


def _build_entry(
    resource: ResourceConfig,
    package_prefix: str,
    model_imports: dict[str, set[str]],
    link_schema_imports: dict[str, set[str]],
    builder_imports: dict[str, set[str]],
) -> dict[str, object]:
    """Build template context for one resource's link entry.

    Mutates the import bags in place so the template can render
    the import block as one sorted line per module.
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
    link_schema_imports.setdefault(schema_module, set()).add(link_schema_class)

    # Always import the model — the ref resolver fetches rows by
    # id even when the link itself is built by a user function.
    model_imports.setdefault(model_module, set()).add(model_name.pascal)
    pk_attr = resource.pk
    resolver_fn_name = f"_resolve_{slug}_refs"

    if link.builder is not None:
        builder_module, _, builder_name = link.builder.rpartition(".")

        if not builder_module or not builder_name:
            msg = (
                f"link.builder for {resource.model!r} must be a "
                f"dotted path (got {link.builder!r})"
            )
            raise ValueError(msg)

        builder_imports.setdefault(builder_module, set()).add(builder_name)
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


def _sorted_imports(
    bag: dict[str, set[str]],
) -> list[tuple[str, list[str]]]:
    """Sort the import bag deterministically: modules then names."""
    return [(mod, sorted(names)) for mod, names in sorted(bag.items())]
