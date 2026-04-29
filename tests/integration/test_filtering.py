"""End-to-end coverage of the structured filter machinery.

Builds a project config whose list op carries the full range of
``StructuredFilterField`` modes (enum, bool, free_text, literal),
runs the generator, and asserts that the rendered routes file
includes the discovery handler, per-field value providers, and
the right imports.  Stops short of executing the generated app
(consumer modules are fictitious) — the unit suites cover op
behavior in isolation; this test guards composition against
regressions in the rendering pipeline.
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


def test_filter_discovery_handler_emitted(files: dict[str, str]) -> None:
    """``GET /_filters`` exists and inlines enum choices via the
    imported enum class."""
    routes = files["inventory/routes/product.py"]

    assert '@router.get("/_filters"' in routes
    assert "async def filters_product(" in routes
    # Enum class imported alongside Product (collector folds the line).
    assert "from inventory.models import" in routes
    assert "Status" in routes
    # Enum members referenced inline in the response.
    assert "for _m in Status" in routes


def test_filter_discovery_payload_shape(files: dict[str, str]) -> None:
    """Each filter field lands in the response with its kind +
    operators."""
    routes = files["inventory/routes/product.py"]

    # Enum field: kind + endpoint + operators.
    assert '"field": "status"' in routes
    assert '"kind": "enum"' in routes
    assert '"operators": ["eq", "in"]' in routes
    assert '"endpoint": "/products/_values/status"' in routes

    # Bool field: just kind.
    assert '"field": "active"' in routes
    assert '"kind": "bool"' in routes

    # Free-text field: kind + endpoint.
    assert '"field": "sku"' in routes
    assert '"kind": "free_text"' in routes
    assert '"endpoint": "/products/_values/sku"' in routes

    # Literal field: kind + type, no endpoint.
    assert '"field": "unit_price"' in routes
    assert '"kind": "literal"' in routes
    assert '"type": "float"' in routes


def test_value_provider_routes_emitted(files: dict[str, str]) -> None:
    """Enum and free-text value-provider POST routes both exist;
    bool and literal don't."""
    routes = files["inventory/routes/product.py"]

    # Enum — uses ingot.filter_values.enum_values.
    assert '@router.post("/_values/status"' in routes
    assert "async def filter_values_product_status(" in routes
    assert "from ingot.filter_values import enum_values" in routes
    assert "return enum_values(Status, body)" in routes

    # Free-text — issues SQL through Product.sku.
    assert '@router.post("/_values/sku"' in routes
    assert "async def filter_values_product_sku(" in routes
    assert "Product.sku.ilike" in routes

    # Bool / literal have no value-provider routes.
    assert '@router.post("/_values/active"' not in routes
    assert '@router.post("/_values/unit_price"' not in routes


def test_filter_values_request_imported(files: dict[str, str]) -> None:
    """The shared body schema is imported once per routes file."""
    routes = files["inventory/routes/product.py"]

    assert "FilterValuesRequest" in routes
    assert "from ingot.filter_values import" in routes


def test_search_endpoint_still_filters(files: dict[str, str]) -> None:
    """The existing list/search execution path still wires
    apply_filters — discovery + value providers don't displace it."""
    routes = files["inventory/routes/product.py"]

    assert '@router.post("/search"' in routes
    assert "from ingot.filters import apply_filters" in routes
    assert "stmt = apply_filters(stmt, body.filter," in routes
