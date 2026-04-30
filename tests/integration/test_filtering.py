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

    # Five endpoints, each declaring a typed response_model so
    # FastAPI/OpenAPI surface real schemas (not bare dicts).
    assert (
        '@router.get("/_filters", response_model=ProjectDiscovery)'
        in router
    )
    assert (
        '@router.get("/_filters/{resource}", response_model=ResourceDiscovery)'
        in router
    )
    assert (
        '@router.get("/_filters/{resource}/{field}", '
        "response_model=FieldDiscovery)"
    ) in router
    assert (
        '@router.post("/_values/{resource}", response_model=ValuesPage)'
        in router
    )
    assert (
        '@router.post("/_values/{resource}/{field}", response_model=ValuesPage)'
        in router
    )

    # Each delegates to the registry.
    assert "resource_registry.discovery()" in router
    assert "resource_registry.discovery(resource=resource)" in router
    assert (
        "resource_registry.discovery(resource=resource, field=field)" in router
    )
    assert "await resource_registry.values(" in router

    # Typed registry models imported alongside the body schema.
    assert "from ingot.filter_values import FilterValuesRequest" in router
    assert "from ingot.resource_registry import" in router
    assert "ProjectDiscovery" in router
    assert "ResourceDiscovery" in router
    assert "FieldDiscovery" in router
    assert "ValuesPage" in router


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
