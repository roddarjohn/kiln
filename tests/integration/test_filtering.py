"""End-to-end coverage of the structured filter machinery.

Filters and value providers no longer emit per-resource routes —
every resource that opts in feeds a single project-wide
:class:`ingot.resource_registry.ResourceRegistry`, and the project-scope
``resource_registry`` op generates one ``resources/__init__.py``
(registry construction) plus one ``resources/router.py`` (five thin
delegating route handlers) at the project root.

This test asserts:

* The registry module imports the right
  :class:`~ingot.resource_registry.ResourceRegistry`,
  :class:`~ingot.resource_registry.ResourceEntry`, model class, enum
  class, and per-app ``LINKS`` map (when ``searchable`` is on).
* Each filter field renders as the matching field-spec dataclass
  with its operators preserved.
* The router file exposes ``GET /_filters``, ``GET /_filters/{resource}``,
  ``GET /_filters/{resource}/{field}``, ``POST /_values/{resource}``,
  and ``POST /_values/{resource}/{field}``, all delegating to
  ``resource_registry``.
* The project router mounts the new filter router.
* The per-resource routes file no longer carries any of the old
  ``/_filters`` or ``/_values`` boilerplate.
* The list endpoint still wires :func:`ingot.filters.apply_filters`
  (the filter modifier still flips ``has_filter`` on the parent list).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import pytest

from be.config.schema import (
    App,
    AppConfig,
    DatabaseConfig,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
)
from be.target import target
from foundry.pipeline import generate

if TYPE_CHECKING:
    from foundry.spec import GeneratedFile


_LIST_FIELDS = [
    {"name": "id", "type": "uuid"},
    {"name": "sku", "type": "str"},
    {"name": "name", "type": "str"},
    {"name": "active", "type": "bool"},
    {"name": "status", "type": "str"},
    {"name": "unit_price", "type": "float"},
]


def _product_resource() -> ResourceConfig:
    """A resource exercising every value-kind except ``ref``."""
    return ResourceConfig(
        model="inventory.models.Product",
        require_auth=False,
        route_prefix="/products",
        operations=[
            OperationConfig(
                name="list",
                fields=_LIST_FIELDS,
                modifiers=[
                    {
                        "type": "filter",
                        "fields": [
                            {
                                "name": "status",
                                "values": "enum",
                                "enum": "inventory.models.Status",
                            },
                            {"name": "active", "values": "bool"},
                            {"name": "sku", "values": "free_text"},
                            {
                                "name": "unit_price",
                                "values": "literal",
                                "type": "float",
                            },
                        ],
                    },
                ],
            ),
        ],
    )


@pytest.fixture
def files() -> dict[str, str]:
    """Run the full pipeline against the structured-filter resource."""
    config = ProjectConfig(
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(
                    module="inventory", resources=[_product_resource()]
                ),
                prefix="/inventory",
            ),
        ],
    )
    rendered: list[GeneratedFile] = generate(config, target)
    return {f.path: f.content for f in rendered}


def test_every_generated_python_file_parses(files: dict[str, str]) -> None:
    """No phase emits broken syntax."""
    for path, content in files.items():
        if not path.endswith(".py"):
            continue

        try:
            ast.parse(content)

        except SyntaxError as exc:  # pragma: no cover -- diagnostic
            msg = f"Generated {path} is not valid Python: {exc}"
            raise AssertionError(msg) from exc


def test_registry_module_emitted(files: dict[str, str]) -> None:
    """The project-scope op produces ``resources/__init__.py``."""
    assert "resources/__init__.py" in files
    registry = files["resources/__init__.py"]

    # ResourceRegistry + ResourceEntry are imported from ingot.
    assert "from ingot.resource_registry import" in registry
    assert "ResourceRegistry" in registry
    assert "ResourceEntry" in registry

    # Resource keyed by lowercased model class name.
    assert '"product": ResourceEntry(' in registry
    assert "model=Product," in registry
    assert 'pk="id",' in registry


def test_registry_field_specs(files: dict[str, str]) -> None:
    """Each filter field renders as its matching dataclass."""
    registry = files["resources/__init__.py"]

    # Enum: imports the enum class and references it by name.
    assert "from inventory.models import" in registry
    assert "Status" in registry
    assert (
        "Enum('status', enum_class=Status, operators=('eq', 'in'))" in registry
    )

    # Bool: just the name + operators.
    assert "Bool('active', operators=('eq',))" in registry

    # FreeText: name + default ('eq', 'contains', 'starts_with') ops.
    assert "FreeText('sku'," in registry
    assert "'contains'" in registry
    assert "'starts_with'" in registry

    # LiteralField: name + scalar type + operators.
    assert "LiteralField('unit_price', type='float'," in registry


def test_router_module_emitted(files: dict[str, str]) -> None:
    """The router file delegates every endpoint to ``resource_registry``."""
    assert "resources/router.py" in files
    router = files["resources/router.py"]

    # Three endpoints, all POST.  Discovery uses the codegen'd
    # discriminated unions; values is a single discriminated POST.
    assert '@router.post("/_filters", response_model=Discovery)' in router
    assert (
        '@router.post("/_filters/fields", response_model=FieldsDiscovery)'
        in router
    )
    assert '@router.post("/_values", response_model=ValuesPage)' in router
    # The two-route shape is gone.
    assert "/_values/{resource}" not in router

    # Each delegates to the registry.
    assert "resource_registry.filter_discovery(" in router
    assert "resource_registry.field_discovery(" in router
    assert "await resource_registry.values(" in router

    # Schemas imported from the codegen'd schemas module.
    assert "from _generated.resources.schemas import" in router
    assert "Discovery" in router
    assert "FieldsDiscovery" in router
    assert "ProjectFilterDiscoveryRequest" in router
    assert "ProjectFieldDiscoveryRequest" in router
    assert "RegisteredValuesRequest" in router
    # Body schema and ValuesPage from ingot.
    assert "from ingot.filter_values import FilterValuesRequest" in router
    assert "ValuesPage" in router


def test_per_resource_schema_module_emitted(files: dict[str, str]) -> None:
    """Each contributing resource gets its own ``<app>/resources/<slug>.py``.

    Per-resource schemas live alongside the rest of the per-app
    generated tree (schemas, serializers, links) so file size
    stays bounded as resources accumulate.
    """
    assert "inventory/resources/product.py" in files
    schema = files["inventory/resources/product.py"]

    # Per-resource Resource class with the slug as discriminator.
    assert "class ProductResource(BaseModel):" in schema
    assert 'resource: Literal["product"] = "product"' in schema
    assert "supports_search: bool" in schema

    # Per-field filter classes with field-name discriminators +
    # narrowed values descriptor types.
    assert "class ProductStatusFilter(BaseModel):" in schema
    assert 'field: Literal["status"] = "status"' in schema
    assert "values: EnumValuesDescriptor" in schema

    assert "class ProductSkuFilter(BaseModel):" in schema
    assert "values: FreeTextValuesDescriptor" in schema

    assert "class ProductActiveFilter(BaseModel):" in schema
    assert "values: BoolValuesDescriptor" in schema

    assert "class ProductUnitPriceFilter(BaseModel):" in schema
    assert "values: LiteralValuesDescriptor" in schema

    # Per-resource discriminated filter union.
    assert "ProductFilter = " in schema

    # Operators land as the FilterOperator enum.
    assert "operators: list[FilterOperator]" in schema

    # Per-resource FieldRef.
    assert "class ProductFieldRef(BaseModel):" in schema


def test_central_schemas_module_re_exports_unions(
    files: dict[str, str],
) -> None:
    """``resources/schemas.py`` imports per-resource classes and assembles
    the project-wide unions + request/response models."""
    assert "resources/schemas.py" in files
    schemas = files["resources/schemas.py"]

    # Imports each per-resource module.
    assert "from _generated.inventory.resources.product import" in schemas

    # Resource slug literal.
    assert 'ResourceSlug = Literal[\n "product"\n]' in schemas or (
        'ResourceSlug = Literal[ "product"' in schemas
    )

    # Project-wide unions and discovery payload.
    assert "RegisteredResource" in schemas
    assert "class Discovery(BaseModel):" in schemas
    assert "resources: list[RegisteredResource]" in schemas

    # Project-narrowed request schemas.
    assert "class ProjectFilterDiscoveryRequest(BaseModel):" in schemas
    assert "resources: list[ResourceSlug] | None = None" in schemas
    assert "class ProjectFieldDiscoveryRequest(BaseModel):" in schemas


def test_project_router_mounts_resource_router(files: dict[str, str]) -> None:
    """``routes/__init__.py`` includes the new filter router."""
    project_router = files["routes/__init__.py"]

    assert (
        "from _generated.resources.router import router as resource_router"
        in (project_router)
    )
    assert "router.include_router(resource_router)" in project_router


def test_per_resource_routes_no_longer_carry_filter_endpoints(
    files: dict[str, str],
) -> None:
    """Old per-resource ``/_filters`` and ``/_values`` are gone."""
    routes = files["inventory/routes/product.py"]

    assert '@router.get("/_filters' not in routes
    assert '@router.post("/_values/' not in routes
    # The list /search endpoint stays untouched.
    assert '@router.post("/search"' in routes


def test_search_endpoint_still_filters(files: dict[str, str]) -> None:
    """The list/search execution path still wires apply_filters —
    the filter modifier flips ``has_filter`` on the parent list
    even though it no longer emits routes itself."""
    routes = files["inventory/routes/product.py"]

    assert "from ingot.filters import apply_filters" in routes
    assert "stmt = apply_filters(stmt, body.filter," in routes


def test_no_filter_modifier_skips_registry_emission() -> None:
    """A project with no filters or searchable resources gets no
    registry / router files at all."""
    config = ProjectConfig(
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(
                    module="inventory",
                    resources=[
                        ResourceConfig(
                            model="inventory.models.Product",
                            require_auth=False,
                            route_prefix="/products",
                            operations=[
                                OperationConfig(
                                    name="get", fields=_LIST_FIELDS
                                ),
                            ],
                        ),
                    ],
                ),
                prefix="/inventory",
            ),
        ],
    )
    files = {f.path: f.content for f in generate(config, target)}

    assert "resources/__init__.py" not in files
    assert "resources/router.py" not in files
    assert "resource_router" not in files["routes/__init__.py"]
