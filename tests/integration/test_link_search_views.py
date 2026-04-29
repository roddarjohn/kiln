"""End-to-end coverage of link / searchable / ref / saved-view emission.

Exercises step 2 (link schemas, resource search endpoint, ref
filter targeting) and step 3 (saved-view CRUD) of the filtering
plan.  Builds two resources that reference each other through a
``ref`` filter and through saved views, runs the generator, and
asserts that:

* Each resource's link config produces an entry in the per-app
  ``links.py`` registry.
* ``searchable: true`` produces a ``POST /_values`` route.
* A ``ref`` filter on one resource points at the *other*
  resource's resource-level ``_values`` URL in the discovery
  payload.
* ``saved_views: true`` produces five CRUD routes (list, create,
  get, update, delete) plus the right imports from
  ``ingot.saved_views``.
* Auth wiring: saved-views handlers receive the session
  parameter via the ``force_session`` path on the auth op.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import pytest

from be.config.schema import (
    App,
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    LinkConfig,
    OperationConfig,
    ProjectConfig,
    ResourceConfig,
)
from be.target import target
from foundry.pipeline import generate

if TYPE_CHECKING:
    from foundry.spec import GeneratedFile


_PRODUCT_FIELDS = [
    {"name": "id", "type": "uuid"},
    {"name": "sku", "type": "str"},
    {"name": "name", "type": "str"},
    {"name": "customer_id", "type": "uuid"},
]


_CUSTOMER_FIELDS = [
    {"name": "id", "type": "uuid"},
    {"name": "name", "type": "str"},
]


def _customer() -> ResourceConfig:
    return ResourceConfig(
        model="inventory.models.Customer",
        route_prefix="/customers",
        searchable=True,
        link=LinkConfig(kind="id_name", name="name"),
        operations=[
            OperationConfig(name="list", fields=_CUSTOMER_FIELDS),
        ],
    )


def _product() -> ResourceConfig:
    """Product references Customer via a ref filter and has saved
    views; Customer is implicitly required to have a link by the
    cross-resource validator."""
    return ResourceConfig(
        model="inventory.models.Product",
        route_prefix="/products",
        saved_views=True,
        link=LinkConfig(kind="id_name", name="name"),
        operations=[
            OperationConfig(
                name="list",
                fields=_PRODUCT_FIELDS,
                modifiers=[
                    {
                        "type": "filter",
                        "fields": [
                            {
                                "name": "customer_id",
                                "values": "ref",
                                "ref_resource": "customer",
                            },
                            {"name": "sku", "values": "free_text"},
                        ],
                    },
                ],
            ),
        ],
    )


@pytest.fixture
def files() -> dict[str, str]:
    """Run the generator over a project with both resources."""
    config = ProjectConfig(
        auth=AuthConfig(
            credentials_schema="myapp.auth.LoginCredentials",
            session_schema="myapp.auth.Session",
            validate_fn="myapp.auth.validate",
        ),
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(
                    module="inventory",
                    resources=[_customer(), _product()],
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


def test_links_registry_emitted(files: dict[str, str]) -> None:
    """Both resources show up in inventory/links.py."""
    links = files["inventory/links.py"]

    assert "from ingot.links import LinkIDName" in links
    assert "from inventory.models import Customer, Product" in links
    # Generated builders for shorthand entries.
    assert "async def _link_customer(" in links
    assert "async def _link_product(" in links
    # LINKS dict at the bottom maps slugs to builders.
    assert '"customer": _link_customer' in links
    assert '"product": _link_product' in links


def test_searchable_emits_resource_values_route(
    files: dict[str, str],
) -> None:
    """Customer's searchable=True produces POST /_values."""
    routes = files["inventory/routes/customer.py"]

    assert '@router.post("/_values"' in routes
    assert "async def values_customer(" in routes
    # Pulls the link builder out of the per-app registry.
    assert 'builder = LINKS["customer"]' in routes
    # ILIKE search uses the link's name field.
    assert "Customer.name.ilike" in routes


def test_ref_filter_points_at_target_values_endpoint(
    files: dict[str, str],
) -> None:
    """Product's customer_id filter targets /customers/_values."""
    routes = files["inventory/routes/product.py"]

    # Discovery payload carries the target resource's _values URL.
    assert '"endpoint": "/customers/_values"' in routes
    assert '"type": "customer"' in routes
    assert '"kind": "ref"' in routes


def test_saved_views_routes_emitted(files: dict[str, str]) -> None:
    """All five CRUD routes show up on the product router."""
    routes = files["inventory/routes/product.py"]

    assert '@router.get("/views"' in routes
    assert '@router.post("/views"' in routes
    assert '@router.get("/views/{view_id}"' in routes
    assert '@router.patch("/views/{view_id}"' in routes
    assert '@router.delete("/views/{view_id}"' in routes
    # Per-user scoping by session.user_id.
    assert "session.user_id" in routes
    # Resource-type discriminator on the shared SavedView table.
    assert 'SavedView.resource_type == "product"' in routes


def test_saved_views_handlers_receive_session(
    files: dict[str, str],
) -> None:
    """Auth's force_session path threads the session dep onto every
    saved-view handler regardless of per-op require_auth."""
    routes = files["inventory/routes/product.py"]

    # Every saved-view handler needs the session for owner_id.
    saved_views_section = routes[routes.index("def saved_views_product_") :]
    assert "session: Annotated[Session, Depends(get_session)]" in (
        saved_views_section
    )
