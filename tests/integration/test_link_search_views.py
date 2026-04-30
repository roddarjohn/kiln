"""End-to-end coverage of link / searchable / ref / saved-view emission.

Exercises link configs, the project-wide search endpoint, ref
filter targeting, and saved views configured as a regular kiln
resource that uses the new ``serializer:`` hook for hydration.
Builds three resources — Customer, Product, and SavedView — and
asserts that:

* Each linked resource produces an entry in the per-app
  ``links.py`` registry, plus a matching ``REF_RESOLVERS`` entry
  capable of fetching rows by id.
* ``searchable: True`` contributes ``search_columns`` to the
  resource's project-wide registry entry — the ``POST /_values``
  endpoint lives on the project router, not on the resource.
* A ``ref`` filter on one resource shows up in the registry with
  the right ``target`` slug; the FE-facing endpoint URL points at
  the project-wide ``/_values`` route.
* A regular CRUD resource carrying ``serializer:`` on its read
  ops swaps in the user's serializer (called with
  ``(obj, session, db)``) and drops ``response_model``.
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
    OperationConfig,
    ProjectConfig,
    RepresentationConfig,
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


_SAVED_VIEW_FIELDS = [
    {"name": "id", "type": "str"},
    {"name": "resource_type", "type": "str"},
    {"name": "name", "type": "str"},
]


_DEFAULT_LINK_FIELDS = [
    {"name": "id", "type": "uuid"},
    {"name": "name", "type": "str"},
]


def _customer() -> ResourceConfig:
    return ResourceConfig(
        model="inventory.models.Customer",
        route_prefix="/customers",
        searchable=True,
        representations=[
            RepresentationConfig(name="default", fields=_DEFAULT_LINK_FIELDS),
        ],
        default_representation="default",
        operations=[
            OperationConfig(name="list", fields=_CUSTOMER_FIELDS),
        ],
    )


def _product() -> ResourceConfig:
    """Product references Customer via a ref filter; Customer
    therefore needs a default_representation by the cross-resource
    validator."""
    return ResourceConfig(
        model="inventory.models.Product",
        route_prefix="/products",
        representations=[
            RepresentationConfig(name="default", fields=_DEFAULT_LINK_FIELDS),
        ],
        default_representation="default",
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


def _saved_view() -> ResourceConfig:
    """SavedView is a normal CRUD resource — kiln has no special
    case for it.  The user wires up:

    * Per-user scoping via ``can`` guards (existing #60 surface).
    * Resource-type filtering via the structured filter machinery.
    * Hydration via a custom ``serializer:`` on read ops, which
      points at user code that calls
      :func:`ingot.saved_views.hydrate_view`.
    """
    return ResourceConfig(
        model="inventory.models.SavedView",
        pk={"name": "id", "type": "str"},
        route_prefix="/saved-views",
        require_auth=True,
        operations=[
            OperationConfig(
                name="get",
                fields=_SAVED_VIEW_FIELDS,
                serializer="inventory.serializers.dump_view_hydrated",
                can="inventory.guards.is_view_owner",
            ),
            OperationConfig(
                name="list",
                fields=_SAVED_VIEW_FIELDS,
                serializer="inventory.serializers.dump_view_hydrated",
                modifiers=[
                    {
                        "type": "filter",
                        "fields": [
                            {"name": "resource_type", "values": "free_text"},
                        ],
                    },
                ],
                can="inventory.guards.is_view_owner",
            ),
            OperationConfig(
                name="create",
                fields=[{"name": "name", "type": "str"}],
            ),
            OperationConfig(
                name="update",
                fields=[{"name": "name", "type": "str"}],
                can="inventory.guards.is_view_owner",
            ),
            OperationConfig(
                name="delete",
                can="inventory.guards.is_view_owner",
            ),
        ],
    )


@pytest.fixture
def files() -> dict[str, str]:
    """Run the generator over a project with all three resources."""
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
                    resources=[_customer(), _product(), _saved_view()],
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
    """Resources with a default representation show up in
    ``inventory/links.py`` with builders *and* ref resolvers,
    importing the per-resource representation schema (named after
    the default representation, e.g. ``CustomerDefault``)."""
    links = files["inventory/links.py"]

    # Per-resource default-rep schemas imported from generated schemas.
    assert (
        "from _generated.inventory.schemas.customer import CustomerDefault"
        in links
    )
    assert (
        "from _generated.inventory.schemas.product import ProductDefault"
        in links
    )
    assert "from inventory.models import Customer, Product" in links
    # Generated builders return the typed default representation.
    assert "async def _link_customer(" in links
    assert "-> CustomerDefault:" in links
    assert "async def _link_product(" in links
    assert "-> ProductDefault:" in links
    # Per-resource ref resolvers used by saved-view hydration.
    assert "async def _resolve_customer_refs(" in links
    assert "async def _resolve_product_refs(" in links
    # Both registries at the bottom of the module.
    assert '"customer": _link_customer' in links
    assert '"customer": _resolve_customer_refs' in links


def test_representation_schema_emitted_in_resource_schemas(
    files: dict[str, str],
) -> None:
    """Each declared representation produces a Pydantic class.  The
    ``type`` field is a ``Literal[<slug>]`` discriminator so payloads
    can be narrowed in cross-resource unions (saved views, ref
    autocomplete)."""
    customer_schemas = files["inventory/schemas/customer.py"]

    assert "class CustomerDefault(BaseModel):" in customer_schemas
    assert 'type: Literal["customer"] = "customer"' in customer_schemas
    assert "id: uuid.UUID" in customer_schemas
    assert "name: str" in customer_schemas

    product_schemas = files["inventory/schemas/product.py"]
    assert "class ProductDefault(BaseModel):" in product_schemas
    assert 'type: Literal["product"] = "product"' in product_schemas


def test_searchable_contributes_search_columns(
    files: dict[str, str],
) -> None:
    """Customer's ``searchable=True`` lands as ``search_columns`` on
    its registry entry (no per-resource ``POST /_values`` route is
    emitted, and link payloads are no longer plumbed through the
    registry — the FE resolves ids via REF_RESOLVERS)."""
    routes = files["inventory/routes/customer.py"]
    assert '@router.post("/_values"' not in routes

    registry = files["resources/__init__.py"]
    # Customer entry exists.  Default search columns fall back to
    # the first ``str``-typed entry in the default representation's
    # ``fields`` (``name``).
    assert '"customer": ResourceEntry(' in registry
    assert "search_columns=('name'" in registry
    # No SearchSpec / LINKS plumbing in the registry anymore.
    assert "SearchSpec(" not in registry
    assert "_inventory_LINKS" not in registry


def test_ref_filter_targets_registered_resource(
    files: dict[str, str],
) -> None:
    """Product's customer_id filter is a Ref into the registry,
    targeting customer (the project-wide ``/_values/customer``
    endpoint serves it)."""
    registry = files["resources/__init__.py"]

    assert "Ref('customer_id', target='customer'" in registry


def test_custom_serializer_replaces_auto_dump(
    files: dict[str, str],
) -> None:
    """SavedView's get/list ops call the user's serializer with
    (obj, session, db) and drop response_model."""
    routes = files["inventory/routes/saved_view.py"]

    # Custom serializer imported by its dotted path, not from the
    # auto-generated serializers module.
    assert "from inventory.serializers import dump_view_hydrated" in routes
    # The route body calls the custom serializer with three args.
    assert "await dump_view_hydrated(obj, session, db)" in routes
    # response_model not set on either read route — the user's
    # function returns dict[str, Any], not the auto schema.
    assert '@router.get("/{id}")\n' in routes, (
        "GET should not carry a response_model kwarg"
    )


def test_auto_serializer_still_works_when_not_overridden(
    files: dict[str, str],
) -> None:
    """Resources that *don't* set ``serializer:`` keep the
    auto-generated dump path.

    Customer's list op inherits the resource's ``default``
    representation (no per-op ``representation:`` or ``fields:``
    override), so the rendered list handler imports the
    representation's auto-generated serializer.
    """
    customer_routes = files["inventory/routes/customer.py"]

    # The representation-driven serializer is imported normally.
    assert "to_customer_default" in customer_routes
