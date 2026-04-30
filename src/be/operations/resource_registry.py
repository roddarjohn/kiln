"""Project-scope op emitting the project-wide resource registry + router.

Walks every resource across every app, collects each one's filter-
modifier fields plus its (optional) ``searchable`` opt-in, and
emits two static files under ``{package_prefix}/resources/``:

* ``__init__.py`` — instantiates a single
  :class:`ingot.resource_registry.ResourceRegistry` with one
  :class:`~ingot.resource_registry.ResourceEntry` per contributing
  resource.  Imports model classes, enum classes, and per-app
  ``LINKS`` maps as needed.
* ``router.py`` — five thin route handlers (``GET /_filters``,
  ``GET /_filters/{resource}``, ``GET /_filters/{resource}/{field}``,
  ``POST /_values/{resource}``, ``POST /_values/{resource}/{field}``)
  that delegate everything to :class:`ResourceRegistry`.

Skipped (via :meth:`~be.operations.resource_registry.ResourceRegistry.when`)
when no resource contributes — keeps configs without filters from
emitting an unused router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from be.config.schema import (
    AppConfig,
    ProjectConfig,
    ResourceConfig,
    StructuredFilterField,
)
from foundry.naming import Name, prefix_import
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext


@operation("resource_registry", scope="project", after_children=True)
class ResourceRegistry:
    """Emit the project-wide resource registry module + router.

    Runs at project scope after every resource has finished so any
    schemas/serializers it transitively depends on are already in
    the store.  This op walks the *config* directly though (not the
    store) — every input it needs is declarative.
    """

    def when(self, ctx: BuildContext[ProjectConfig, ProjectConfig]) -> bool:
        """Skip when no resource contributes filters or search."""
        return any(_iter_contributing_resources(ctx.instance))

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the registry and router static files.

        Args:
            ctx: Build context; instance is the project config.
            _options: Unused.

        Yields:
            Two :class:`~foundry.outputs.StaticFile` entries.

        """
        config = ctx.instance
        package_prefix = config.package_prefix

        registry_imports: list[tuple[str, str]] = [
            ("ingot.resource_registry", "ResourceRegistry"),
            ("ingot.resource_registry", "ResourceEntry"),
        ]
        used_field_kinds: set[str] = set()
        link_aliases: dict[str, str] = {}
        entries: list[dict[str, object]] = []

        for app, resource in _iter_contributing_resources(config):
            entry = _build_entry(
                app=app,
                resource=resource,
                package_prefix=package_prefix,
                imports=registry_imports,
                kinds=used_field_kinds,
                link_aliases=link_aliases,
            )
            entries.append(entry)

        registry_imports.extend(
            ("ingot.resource_registry", kind)
            for kind in sorted(used_field_kinds)
        )

        if any(e["search"] for e in entries):
            registry_imports.append(("ingot.resource_registry", "SearchSpec"))

        # Per-resource per-field schema specs are derived from the
        # same FilterField list that drives the registry entries.
        # One file per resource (under each app's tree) keeps the
        # generated schemas browsable as the resource count grows.
        schema_entries = []
        resource_imports: list[str] = []

        for app, resource in _iter_contributing_resources(config):
            entry = _build_schema_entry(resource)
            schema_entries.append(entry)
            slug = str(entry["slug"])
            module_path = prefix_import(
                package_prefix, app.module, "resources", slug
            )
            resource_imports.append(
                f"from {module_path} import "
                f"{entry['resource_class']}, {entry['filter_union']}, "
                f"{entry['pascal']}FieldRef, {entry['pascal']}ValuesRequest"
            )

            yield StaticFile(
                path=f"{app.module}/resources/{slug}.py",
                template="fastapi/init/resource_schema.py.j2",
                context={"entry": entry},
            )

        union_sources = _format_top_level_unions(schema_entries)

        yield StaticFile(
            path="resources/__init__.py",
            template="fastapi/init/resource_registry.py.j2",
            context={
                "imports": _format_imports(registry_imports),
                "entries": entries,
            },
        )

        yield StaticFile(
            path="resources/schemas.py",
            template="fastapi/init/resource_registry_schemas.py.j2",
            context={
                "resource_imports": resource_imports,
                "entries": schema_entries,
                **union_sources,
            },
        )

        require_auth = _resolve_require_auth(config)
        primary_db = config.resolve_database(None)
        session_module = prefix_import(
            package_prefix, primary_db.session_module
        )

        auth_module: str | None = None
        session_schema_module: str | None = None
        session_schema_name: str | None = None

        if require_auth:
            if config.auth is None:
                msg = (
                    "resource_registry.require_auth=True but the project "
                    "has no auth configured.  Set project.auth or set "
                    "resource_registry.require_auth=False."
                )
                raise ValueError(msg)

            auth_module = (
                f"{package_prefix}.auth.dependencies"
                if package_prefix
                else "auth.dependencies"
            )
            sess_mod, sess_name_obj = Name.from_dotted(
                config.auth.session_schema
            )
            session_schema_module = sess_mod
            session_schema_name = sess_name_obj.raw

        registry_module = (
            f"{package_prefix}.resources" if package_prefix else "resources"
        )

        yield StaticFile(
            path="resources/router.py",
            template="fastapi/init/resource_registry_router.py.j2",
            context={
                "registry_module": registry_module,
                "schemas_module": f"{registry_module}.schemas",
                "session_module": session_module,
                "get_db_fn": primary_db.get_db_fn,
                "require_auth": require_auth,
                "auth_module": auth_module,
                "session_schema_module": session_schema_module,
                "session_schema_name": session_schema_name,
            },
        )


# -------------------------------------------------------------------
# Helpers — collection + per-resource entry assembly.
# -------------------------------------------------------------------


def _resolve_require_auth(config: ProjectConfig) -> bool:
    """Resolve the effective ``require_auth`` for the registry routes.

    Honors an explicit setting in :attr:`ProjectConfig.resource_registry`
    when present; otherwise infers from whether the project has auth
    configured at all.
    """
    explicit = config.resource_registry.require_auth

    if explicit is not None:
        return explicit

    return config.auth is not None


def _iter_contributing_resources(
    config: ProjectConfig,
) -> Iterable[tuple[AppConfig, ResourceConfig]]:
    """Yield ``(app, resource)`` pairs that need a registry entry.

    A resource contributes when it has at least one ``filter``
    modifier under any of its operations, or opts into
    :attr:`ResourceConfig.searchable`.
    """
    for app in config.apps:
        for resource in app.config.resources:
            if _resource_filter_fields(resource) or resource.searchable:
                yield app.config, resource


def _resource_filter_fields(
    resource: ResourceConfig,
) -> list[StructuredFilterField]:
    """Return the structured filter fields declared on *resource*.

    Walks each op's modifier list, picks the first ``filter``
    modifier (the only one we generate routes for today), and parses
    its ``fields`` into :class:`StructuredFilterField` instances so
    the registry op gets the same validated shape the per-list
    Filter op consumes.
    """
    for op in resource.operations:
        for modifier in op.modifiers:
            if modifier.type != "filter":
                continue

            raw: list[object] = modifier.options.get("fields") or []
            return [StructuredFilterField.model_validate(f) for f in raw]

    return []


def _build_entry(  # noqa: PLR0913 -- collects six shared accumulators
    *,
    app: AppConfig,
    resource: ResourceConfig,
    package_prefix: str,
    imports: list[tuple[str, str]],
    kinds: set[str],
    link_aliases: dict[str, str],
) -> dict[str, object]:
    """Build the template-context dict for one resource's entry.

    Side effects:

    * Appends model and enum imports onto *imports*.
    * Records each ``FilterField`` kind used so the registry module
      imports only what it needs.
    * Allocates a per-app ``LINKS`` alias on *link_aliases* (and
      the corresponding import) when the resource opts into
      ``searchable``.
    """
    model_module, model = Name.from_dotted(resource.model)
    slug = model.lower

    imports.append((model_module, model.pascal))

    fields_src: list[str] = []

    for field in _resource_filter_fields(resource):
        rendered, kind = _render_field(field, slug, imports)
        kinds.add(kind)
        fields_src.append(rendered)

    search_src: str | None = None

    if resource.searchable:
        link = resource.link

        if link is None:  # pragma: no cover -- validator catches this
            msg = (
                f"Resource {resource.model!r}: searchable=True without "
                f"a link config; cross-resource validator should have "
                f"caught this."
            )
            raise ValueError(msg)

        columns = (
            list(resource.search.fields)
            if resource.search is not None
            else _default_search_fields(link)
        )
        vector_column = (
            resource.search.vector_column
            if resource.search is not None
            else None
        )
        alias = link_aliases.get(app.module)

        if alias is None:
            alias = f"_{app.module}_LINKS"
            link_aliases[app.module] = alias
            links_module = prefix_import(package_prefix, app.module, "links")
            imports.append((links_module, f"LINKS as {alias}"))

        columns_src = ", ".join(repr(c) for c in columns)

        if len(columns) == 1:
            columns_src += ","

        vector_kwarg = (
            f", vector_column={vector_column!r}"
            if vector_column is not None
            else ""
        )

        search_src = (
            f"SearchSpec(columns=({columns_src}), "
            f"link={alias}[{slug!r}]{vector_kwarg})"
        )

    return {
        "slug": slug,
        "model_name": model.pascal,
        "pk": resource.pk,
        "fields": fields_src,
        "search": search_src,
    }


_VALUES_DESCRIPTOR_BY_KIND: dict[str, str] = {
    "enum": "EnumValuesDescriptor",
    "free_text": "FreeTextValuesDescriptor",
    "ref": "RefValuesDescriptor",
    # ``values: "self"`` is a config-side spelling for "Ref pointing
    # at this resource's own slug" — the registry-side descriptor is
    # the same RefValuesDescriptor.
    "self": "RefValuesDescriptor",
    "literal": "LiteralValuesDescriptor",
    "bool": "BoolValuesDescriptor",
}
"""Map a :data:`be.config.schema.FilterValueKind` to the matching
ingot ``ValuesDescriptor`` subclass.  Used to emit a narrowed
``values`` annotation per generated field schema — so the FE
client knows that, e.g., ``ProjectIdFilter.values`` is always
``RefValuesDescriptor`` rather than the open union."""


def _build_schema_entry(
    resource: ResourceConfig,
) -> dict[str, object]:
    """Build the schema-template context for one resource.

    Collects the per-field class names, narrowed ``values``
    descriptor type, per-resource union/class names, and the
    minimal set of ``ValuesDescriptor`` subclasses to import (only
    the ones actually referenced by this resource's fields, so
    each generated module imports just what it needs).
    """
    _, model = Name.from_dotted(resource.model)
    slug = model.lower
    pascal = model.pascal

    field_schemas: list[dict[str, object]] = []
    member_class_names: list[str] = []
    field_literals: list[str] = []
    descriptor_imports: set[str] = set()

    for field in _resource_filter_fields(resource):
        field_pascal = Name(field.name).pascal
        class_name = f"{pascal}{field_pascal}Filter"
        descriptor_class = _VALUES_DESCRIPTOR_BY_KIND[field.values]
        field_schemas.append(
            {
                "class_name": class_name,
                "field_name": field.name,
                "values_class": descriptor_class,
            }
        )
        member_class_names.append(class_name)
        field_literals.append(repr(field.name))
        descriptor_imports.add(descriptor_class)

    field_literal_src = ", ".join(field_literals) if field_literals else None
    # ``Annotated[X, Field(discriminator=...)]`` only makes sense for
    # multi-member unions; a single-member "union" renders as a bare
    # type alias and skips the Annotated wrapper.
    filter_union_src = (
        " | ".join(member_class_names) if len(member_class_names) > 1 else None
    )

    return {
        "slug": slug,
        "pascal": pascal,
        "resource_class": f"{pascal}Resource",
        "filter_union": f"{pascal}Filter",
        "field_schemas": field_schemas,
        "filter_union_src": filter_union_src,
        "field_literal_src": field_literal_src,
        "has_search": resource.searchable,
        "descriptor_imports": sorted(descriptor_imports),
    }


def _format_top_level_unions(
    entries: list[dict[str, object]],
) -> dict[str, str]:
    """Render the project-wide discriminated union expressions.

    Pulled out of Jinja because Python's ``" | ".join(...)`` reads
    cleaner than the equivalent loop with whitespace-control tags.
    Returns the right-hand side of four module-level assignments,
    pre-rendered:

    * ``registered_resource_src`` — for ``RegisteredResource = ...``
    * ``registered_field_ref_src`` — for ``RegisteredFieldRef = ...``
    * ``registered_values_request_src`` — for ``RegisteredValuesRequest = ...``
    * ``fields_discovery_inner_src`` — the element type for
      ``FieldsDiscovery.fields: list[...]``.
    """
    resource_classes = [str(entry["resource_class"]) for entry in entries]
    field_ref_classes = [f"{entry['pascal']}FieldRef" for entry in entries]
    values_request_classes = [
        f"{entry['pascal']}ValuesRequest" for entry in entries
    ]
    filter_unions = [
        str(entry["filter_union"])
        for entry in entries
        if entry["field_schemas"]
    ]

    return {
        "registered_resource_src": _join_union(resource_classes),
        "registered_field_ref_src": _join_union(field_ref_classes),
        "registered_values_request_src": _join_union(values_request_classes),
        "fields_discovery_inner_src": (
            " | ".join(filter_unions) if filter_unions else "ValuesDescriptor"
        ),
    }


def _join_union(members: list[str]) -> str:
    """Render a Python union expression from *members*.

    A single-member "union" is just the bare class name (no
    ``Annotated`` / ``|``); two or more members render as the
    ``A | B | C`` shape Pydantic uses for discriminated unions.
    """
    if len(members) == 1:
        return members[0]

    return " | ".join(members)


def _default_search_fields(link: object) -> list[str]:
    """Mirror :class:`~be.operations.searchable.Searchable`'s old default.

    When a resource opts into ``searchable`` without an explicit
    :attr:`SearchConfig.fields`, the resource-level search uses the
    link's ``name`` column (when shorthand and named) and skips
    ``q``-filtering otherwise.
    """
    name = getattr(link, "name", None)
    builder = getattr(link, "builder", None)

    if builder is None and name:
        return [name]

    return []


def _render_field(
    field: StructuredFilterField,
    resource_slug: str,
    imports: list[tuple[str, str]],
) -> tuple[str, str]:
    """Render one ``StructuredFilterField`` as a constructor expression.

    Side effect: appends the enum-class import for ``values: "enum"``
    fields so the registry module can reference the class by name.

    Returns:
        ``(source, kind_class)`` — the Python expression to drop
        into the entry's ``fields=(...)`` tuple, plus the
        :mod:`ingot.resource_registry` class name to import.

    """
    operators = ", ".join(repr(op) for op in field.operators)
    operators_src = (
        f"({operators},)" if len(field.operators) == 1 else f"({operators})"
    )

    if field.values == "enum":
        enum_dotted = field.enum or ""
        enum_module, enum_name = Name.from_dotted(enum_dotted)
        imports.append((enum_module, enum_name.raw))
        return (
            f"Enum({field.name!r}, enum_class={enum_name.raw}, "
            f"operators={operators_src})",
            "Enum",
        )

    if field.values == "free_text":
        return (
            f"FreeText({field.name!r}, operators={operators_src})",
            "FreeText",
        )

    if field.values == "ref":
        return (
            f"Ref({field.name!r}, target={field.ref_resource!r}, "
            f"operators={operators_src})",
            "Ref",
        )

    if field.values == "self":
        # ``self`` is a config-side spelling of "Ref pointing at my
        # own slug" — codegen translates it to a Ref so the
        # registry only ever sees one shape.
        return (
            f"Ref({field.name!r}, target={resource_slug!r}, "
            f"operators={operators_src})",
            "Ref",
        )

    if field.values == "literal":
        return (
            f"LiteralField({field.name!r}, type={field.type!r}, "
            f"operators={operators_src})",
            "LiteralField",
        )

    # bool
    return (
        f"Bool({field.name!r}, operators={operators_src})",
        "Bool",
    )


def _format_imports(imports: list[tuple[str, str]]) -> list[str]:
    """Group ``(module, name)`` pairs into ``from X import a, b, c`` lines.

    Stable ordering (sorted by module then alias) keeps the
    generated file diff-stable under regen.  Aliases (``"X as Y"``)
    are passed through verbatim so callers can disambiguate
    per-app ``LINKS`` symbols.
    """
    grouped: dict[str, list[str]] = {}

    for module, name in imports:
        grouped.setdefault(module, []).append(name)

    lines: list[str] = []

    for module in sorted(grouped):
        names = sorted(set(grouped[module]))
        joined = ", ".join(names)
        lines.append(f"from {module} import {joined}")

    return lines
