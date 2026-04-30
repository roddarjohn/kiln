"""End-to-end coverage of named representations on read + write ops.

Phase 1 (link → representations) is exercised through
``test_link_search_views``; this module focuses on the per-op
selection added in Phase 2 (``get``/``list`` consume a
representation) and Phase 3 (``create``/``update`` return one).
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


_DEFAULT_FIELDS = [
    {"name": "id", "type": "uuid"},
    {"name": "name", "type": "str"},
]
_DETAIL_FIELDS = [
    {"name": "id", "type": "uuid"},
    {"name": "name", "type": "str"},
    {"name": "summary", "type": "str"},
    {"name": "weight", "type": "float"},
]


def _project(resources: list[ResourceConfig]) -> ProjectConfig:
    return ProjectConfig(
        auth=AuthConfig(
            credentials_schema="myapp.auth.LoginCredentials",
            session_schema="myapp.auth.Session",
            validate_fn="myapp.auth.validate",
        ),
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(module="catalog", resources=resources),
                prefix="/catalog",
            ),
        ],
    )


def _generate(resources: list[ResourceConfig]) -> dict[str, str]:
    rendered: list[GeneratedFile] = generate(_project(resources), target)
    return {f.path: f.content for f in rendered}


# ---------------------------------------------------------------------------
# Phase 1 carry-over: each declared rep produces its own schema class
# ---------------------------------------------------------------------------


def test_each_representation_emits_a_pydantic_class() -> None:
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                    RepresentationConfig(name="detail", fields=_DETAIL_FIELDS),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(name="get", representation="detail"),
                ],
            ),
        ],
    )

    schemas = files["catalog/schemas/widget.py"]
    assert "class WidgetDefault(BaseModel):" in schemas
    assert "class WidgetDetail(BaseModel):" in schemas
    # Both schemas carry the discriminator.
    assert schemas.count('type: Literal["widget"] = "widget"') == 2

    serializers = files["catalog/serializers/widget.py"]
    assert "async def to_widget_default(" in serializers
    assert "async def to_widget_detail(" in serializers


# ---------------------------------------------------------------------------
# Phase 2: get / list pick a representation
# ---------------------------------------------------------------------------


def test_get_uses_explicit_representation() -> None:
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                    RepresentationConfig(name="detail", fields=_DETAIL_FIELDS),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(name="get", representation="detail"),
                ],
            ),
        ],
    )
    routes = files["catalog/routes/widget.py"]
    ast.parse(routes)

    # Response model is the rep schema, imported from the schemas module.
    assert "response_model=WidgetDetail" in routes
    assert (
        "from _generated.catalog.schemas.widget import WidgetDetail" in routes
    )
    # The generated rep serializer is awaited with the session.
    assert "to_widget_detail(obj, session)" in routes
    assert (
        "from _generated.catalog.serializers.widget import to_widget_detail"
        in routes
    )


def test_get_inherits_default_representation_when_not_set() -> None:
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                ],
                default_representation="default",
                operations=[OperationConfig(name="get")],
            ),
        ],
    )
    routes = files["catalog/routes/widget.py"]
    assert "response_model=WidgetDefault" in routes
    assert "to_widget_default(obj, session)" in routes


def test_list_uses_representation() -> None:
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                    RepresentationConfig(name="detail", fields=_DETAIL_FIELDS),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(name="list", representation="detail"),
                ],
            ),
        ],
    )
    routes = files["catalog/routes/widget.py"]
    ast.parse(routes)

    # response_model = list[<rep>]; serializer is awaited per row.
    assert "response_model=list[WidgetDetail]" in routes
    assert "await to_widget_detail(obj, session)" in routes


def test_get_legacy_fields_path_still_works() -> None:
    """A get op without ``representation:`` and without
    ``default_representation`` falls back to ad-hoc per-op fields
    so existing configs keep building."""
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                operations=[
                    OperationConfig(name="get", fields=_DEFAULT_FIELDS),
                ],
            ),
        ],
    )
    schemas = files["catalog/schemas/widget.py"]
    assert "class WidgetResource(BaseModel):" in schemas

    routes = files["catalog/routes/widget.py"]
    assert "response_model=WidgetResource" in routes


def test_unknown_representation_rejected() -> None:
    """Pointing at a name that doesn't match any declared rep
    surfaces as a clear error at build time."""
    from foundry.errors import GenerationError

    config = _project(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(name="get", representation="missing"),
                ],
            ),
        ],
    )

    with pytest.raises(GenerationError, match=r"representation='missing'"):
        generate(config, target)


# ---------------------------------------------------------------------------
# Phase 3: create / update return a representation
# ---------------------------------------------------------------------------


def test_create_returns_explicit_representation() -> None:
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(
                        name="create",
                        fields=[{"name": "name", "type": "str"}],
                        representation="default",
                    ),
                ],
            ),
        ],
    )
    routes = files["catalog/routes/widget.py"]
    ast.parse(routes)

    # Response model lands on the create handler.
    assert "response_model=WidgetDefault" in routes
    # The schema is imported (possibly alongside the request schema).
    assert "from _generated.catalog.schemas.widget import" in routes
    assert "WidgetDefault" in routes
    # ``insert(...).returning(Widget)`` is required to capture the
    # row before the rep builder runs.
    assert ".returning(Widget)" in routes
    # Rep serializer is awaited with session and obj.
    assert "return await to_widget_default(obj, session)" in routes


def test_create_without_representation_returns_no_body() -> None:
    """Today's behaviour: create with no representation set issues
    201 with an empty body and no ``.returning`` clause."""
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(
                        name="create",
                        fields=[{"name": "name", "type": "str"}],
                    ),
                ],
            ),
        ],
    )
    routes = files["catalog/routes/widget.py"]
    create_block = routes[routes.index("async def create_widget(") :]
    next_handler = create_block.find("\n@router", 1)
    create_block = (
        create_block if next_handler == -1 else create_block[:next_handler]
    )

    assert ".returning(Widget)" not in create_block
    assert "to_widget_default" not in create_block


def test_update_returns_explicit_representation() -> None:
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(
                        name="update",
                        fields=[{"name": "name", "type": "str"}],
                        representation="default",
                    ),
                ],
            ),
        ],
    )
    routes = files["catalog/routes/widget.py"]
    ast.parse(routes)

    update_block = routes[routes.index("async def update_widget(") :]
    next_handler = update_block.find("\n@router", 1)
    update_block = (
        update_block if next_handler == -1 else update_block[:next_handler]
    )

    assert "response_model=WidgetDefault" in routes
    assert ".returning(Widget)" in update_block
    assert "return await to_widget_default(obj, session)" in update_block


def test_write_op_does_not_inherit_default_representation() -> None:
    """Default rep is the cross-resource shape (saved-view, ref);
    write ops must opt in explicitly to avoid silently turning every
    create/update into a 201-with-body for any resource that
    declares reps."""
    files = _generate(
        [
            ResourceConfig(
                model="catalog.models.Widget",
                require_auth=False,
                representations=[
                    RepresentationConfig(
                        name="default", fields=_DEFAULT_FIELDS
                    ),
                ],
                default_representation="default",
                operations=[
                    OperationConfig(
                        name="create",
                        fields=[{"name": "name", "type": "str"}],
                    ),
                ],
            ),
        ],
    )
    routes = files["catalog/routes/widget.py"]
    create_block = routes[routes.index("async def create_widget(") :]
    next_handler = create_block.find("\n@router", 1)
    create_block = (
        create_block if next_handler == -1 else create_block[:next_handler]
    )
    assert "response_model" not in create_block.split("def create_widget")[0]
