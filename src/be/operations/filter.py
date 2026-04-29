"""Filter extension: emits filter schemas, discovery, and value providers.

Runs at modifier scope with ``type: "filter"`` as a nested child
of a list op.  Emits:

* ``{Model}FilterCondition`` and ``{Model}FilterExpression``
  schemas (used by the parent list's ``SearchRequest`` body), and
  flips ``has_filter`` on the parent list's request schema and
  search handler so the generated route calls
  :func:`ingot.filters.apply_filters`.
* ``GET /_filters`` — discovery payload describing the resource's
  filterable fields, their operators, and where to fetch values.
* ``POST /_values/{field}`` — one route per ``enum`` and
  ``free_text`` filterable field, populating filter inputs on the
  FE.
"""

from typing import TYPE_CHECKING, cast

from be.config.schema import (
    FilterConfig,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
    StructuredFilterField,
)
from be.operations.list import ListResult, resource_model
from be.operations.types import RouteHandler, RouteParam, SchemaClass, TestCase
from foundry.naming import Name
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from be.config.schema import ModifierConfig
    from foundry.engine import BuildContext


@operation(
    "filter",
    scope="modifier",
    dispatch_on="type",
)
class Filter:
    """Amend the list op with filterable fields.

    Runs at modifier scope — filter configs nest inside a specific
    list op, so the engine descends into List first, then into its
    modifiers in order.  No sibling lookup, no ambiguity with
    multiple lists per resource.
    """

    Options = FilterConfig

    def build(
        self,
        ctx: BuildContext[ModifierConfig, ProjectConfig],
        options: FilterConfig,
    ) -> Iterable[object]:
        """Emit filter schemas, discovery, and value providers.

        Args:
            ctx: Build context for the ``"filter"`` op entry.
            options: Parsed :class:`~be.config.schema.FilterConfig`.

        Yields:
            FilterCondition schema, the discovery RouteHandler, one
            value-provider RouteHandler per enum/free_text field,
            and matching TestCases.  The expression schema renders
            inside the FilterCondition template.

        """
        model = resource_model(ctx)
        result = ctx.store.output_under_ancestor(
            ctx.instance_id, "operation", ListResult
        )
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        list_op = cast(
            "OperationConfig",
            ctx.store.ancestor_of(ctx.instance_id, "operation"),
        )

        list_field_names = [f.name for f in result.list_item.fields]
        structured = options.normalized_fields(list_field_names)
        allowed = [f.name for f in structured]
        sort_payload = _sort_payload(list_op)

        yield SchemaClass(
            name=model.suffixed("FilterCondition"),
            body_template="fastapi/schema_parts/filter_node.py.j2",
            body_context={
                "model_name": model.pascal,
                "allowed_fields": allowed,
            },
            extra_imports=[
                ("typing", "Any"),
                ("typing", "Literal"),
                ("pydantic", "ConfigDict"),
                ("pydantic", "Field"),
            ],
        )

        result.search_request.body_context["has_filter"] = True
        result.handler.body_context["has_filter"] = True
        result.handler.extra_imports.append(
            ("ingot.filters", "apply_filters"),
        )

        yield from _emit_discovery(
            ctx.config, model, resource, structured, sort_payload
        )
        yield from _emit_value_providers(model, resource, structured)


def _filter_payload(
    field: StructuredFilterField,
    resource_prefix: str,
    project: ProjectConfig,
) -> dict[str, object]:
    """Render-time descriptor for one filter field.

    Stripped to JSON-serializable primitives so the discovery
    template can ``tojson`` it without introspection — except the
    ``enum`` case, which references the imported enum class by
    name and is handled in the template itself.

    For ``ref`` fields the endpoint is resolved against *project*
    so it points at the *target* resource's resource-level
    ``_values`` route, honoring its ``route_prefix`` override.
    """
    values: dict[str, object] = {"kind": field.values}

    if field.values == "enum":
        values["enum_class"] = (field.enum or "").rsplit(".", 1)[-1]
        values["endpoint"] = f"{resource_prefix}/_values/{field.name}"

    elif field.values == "free_text":
        values["endpoint"] = f"{resource_prefix}/_values/{field.name}"

    elif field.values == "literal":
        values["type"] = field.type

    elif field.values == "ref":
        values["type"] = field.ref_resource

        endpoint = _resolve_ref_endpoint(project, field.ref_resource or "")

        if endpoint is not None:
            values["endpoint"] = endpoint

    return {
        "field": field.name,
        "operators": list(field.operators),
        "values": values,
    }


def _resource_prefix(resource: ResourceConfig) -> str:
    """Return *resource*'s URL prefix as it appears on the router.

    Mirrors the default in :class:`~be.operations.scaffold` —
    explicit ``route_prefix`` wins, otherwise the lowercased model
    class name with a trailing ``s``.
    """
    if resource.route_prefix is not None:
        return resource.route_prefix

    _, _, class_name = resource.model.rpartition(".")

    return f"/{class_name.lower()}s"


def _resolve_ref_endpoint(project: ProjectConfig, ref_slug: str) -> str | None:
    """Locate *ref_slug*'s resource and return its ``_values`` URL.

    Returns ``None`` when no resource matches.  The cross-resource
    validator on :class:`~be.config.schema.ProjectConfig` catches
    dangling refs at config-load time, so this fallback only fires
    when discovery runs against a config that bypassed validation
    (rare in practice).
    """
    for app in project.apps:
        for resource in app.config.resources:
            _, _, class_name = resource.model.rpartition(".")

            if class_name.lower() == ref_slug:
                return f"{_resource_prefix(resource)}/_values"

    return None


def _sort_payload(list_op: OperationConfig) -> dict[str, object] | None:
    """Pull sort metadata off the list op's order modifier, if any.

    Returns ``None`` when the list op has no order modifier — the
    discovery payload then omits the ``sort`` block entirely.
    """
    for modifier in list_op.modifiers:
        if modifier.type != "order":
            continue

        opts = modifier.options
        fields: list[object] = list(opts.get("fields") or [])
        default = opts.get("default")
        default_dir = opts.get("default_dir", "asc")
        return {
            "fields": fields,
            "default": default,
            "default_dir": default_dir,
        }

    return None


def _emit_discovery(
    project: ProjectConfig,
    model: Name,
    resource: ResourceConfig,
    structured: list[StructuredFilterField],
    sort_payload: dict[str, object] | None,
) -> Iterable[object]:
    """Emit the discovery handlers for the resource's filters.

    Renders both ``GET /_filters`` (full payload) and
    ``GET /_filters/{field}`` (single-filter lazy fetch).  Each
    filter field becomes a JSON-serializable descriptor via
    :func:`_filter_payload`, except ``enum`` choices which the
    template inlines as a comprehension over the imported enum
    class so the response reflects the enum at request time.
    """
    prefix = _resource_prefix(resource)
    payloads = [_filter_payload(f, prefix, project) for f in structured]
    enum_imports: list[tuple[str, str]] = []

    for field in structured:
        if field.values == "enum" and field.enum:
            module, _, cls_name = field.enum.rpartition(".")
            enum_imports.append((module, cls_name))

    yield RouteHandler(
        method="GET",
        path="/_filters",
        function_name=f"filters_{model.lower}",
        op_name="filter",
        params=[],
        return_type="dict[str, Any]",
        doc=(
            f"Discovery payload describing filterable fields on {model.pascal}."
        ),
        body_template="fastapi/ops/filter_discovery.py.j2",
        body_context={
            "filters": payloads,
            "sort": sort_payload,
        },
        extra_imports=[
            ("typing", "Any"),
            *enum_imports,
        ],
    )

    yield TestCase(
        op_name="filter",
        method="get",
        path="/_filters",
        status_success=200,
        action_name="filter_discovery",
    )

    yield RouteHandler(
        method="GET",
        path="/_filters/{field}",
        function_name=f"filter_field_{model.lower}",
        op_name="filter",
        params=[RouteParam(name="field", annotation="str")],
        return_type="dict[str, Any]",
        doc=(
            f"Discovery payload for one named filterable field on "
            f"{model.pascal}.  Returns 404 when the field is "
            f"unknown."
        ),
        body_template="fastapi/ops/filter_field_discovery.py.j2",
        body_context={
            "filters": payloads,
        },
        extra_imports=[
            ("typing", "Any"),
            ("fastapi", "HTTPException"),
            *enum_imports,
        ],
    )

    yield TestCase(
        op_name="filter",
        method="get",
        path="/_filters/{field}",
        status_success=200,
        status_not_found=404,
        action_name="filter_field_discovery",
    )


def _emit_value_providers(
    model: Name,
    resource: ResourceConfig,
    structured: list[StructuredFilterField],
) -> Iterable[object]:
    """Emit ``POST /_values/{field}`` for each enum/free_text field.

    Bool and literal kinds have no value provider — the FE renders
    them natively from the discovery payload.  Ref delegates to the
    target resource's resource-level provider, which is wired in a
    later step (no ref support in this phase).
    """
    model_module, _ = Name.from_dotted(resource.model)

    for field in structured:
        if field.values == "enum":
            module, _, cls_name = (field.enum or "").rpartition(".")
            yield RouteHandler(
                method="POST",
                path=f"/_values/{field.name}",
                function_name=(f"filter_values_{model.lower}_{field.name}"),
                op_name="filter",
                params=[
                    RouteParam(
                        name="body",
                        annotation="FilterValuesRequest",
                    ),
                ],
                return_type="dict[str, Any]",
                doc=(
                    f"Filter values for {model.pascal}.{field.name} "
                    f"(enum {cls_name})."
                ),
                body_template="fastapi/ops/filter_values_enum.py.j2",
                body_context={"enum_class": cls_name},
                extra_imports=[
                    ("typing", "Any"),
                    (module, cls_name),
                    ("ingot.filter_values", "FilterValuesRequest"),
                ],
            )

            yield TestCase(
                op_name="filter",
                method="post",
                path=f"/_values/{field.name}",
                status_success=200,
                has_request_body=True,
                request_schema="FilterValuesRequest",
                action_name=f"filter_values_{field.name}",
            )

        elif field.values == "free_text":
            yield RouteHandler(
                method="POST",
                path=f"/_values/{field.name}",
                function_name=(f"filter_values_{model.lower}_{field.name}"),
                op_name="filter",
                params=[
                    RouteParam(
                        name="body",
                        annotation="FilterValuesRequest",
                    ),
                ],
                return_type="dict[str, Any]",
                doc=(
                    f"Filter values for {model.pascal}.{field.name} "
                    f"(free-text search)."
                ),
                body_template="fastapi/ops/filter_values_free_text.py.j2",
                body_context={
                    "model_name": model.pascal,
                    "field_name": field.name,
                },
                extra_imports=[
                    ("typing", "Any"),
                    ("sqlalchemy", "select"),
                    (model_module, model.pascal),
                    ("ingot.filter_values", "FilterValuesRequest"),
                ],
            )

            yield TestCase(
                op_name="filter",
                method="post",
                path=f"/_values/{field.name}",
                status_success=200,
                has_request_body=True,
                request_schema="FilterValuesRequest",
                action_name=f"filter_values_{field.name}",
            )
