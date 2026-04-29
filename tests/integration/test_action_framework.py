"""End-to-end coverage of the action framework.

Builds a project config that opts in to every surface the
framework exposes -- ``include_actions_in_dump``,
``permissions_endpoint``, per-op ``can`` gates on CRUD ops, plus a
custom action -- runs the full generation pipeline, and asserts
the rendered code contains the expected wiring.  Stops short of
executing the generated FastAPI app (the consumer modules it
references are fictitious): the unit suites already cover op
behavior in isolation; this test guards the *composition* of all
four surfaces against regressions in the rendering pipeline.
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
    ResourceConfig,
)
from be.target import target
from foundry.pipeline import generate

if TYPE_CHECKING:
    from foundry.spec import GeneratedFile


_FIELDS = [
    {"name": "id", "type": "uuid"},
    {"name": "title", "type": "str"},
]


def _read_op(name: str, *, can: str | None = None) -> OperationConfig:
    """Read op (get/list) carrying the standard id+title fields."""
    return OperationConfig(name=name, fields=_FIELDS, can=can)


def _write_op(name: str, *, can: str | None = None) -> OperationConfig:
    """Write op (create/update) carrying the title field."""
    return OperationConfig(
        name=name, fields=[{"name": "title", "type": "str"}], can=can
    )


def _post_resource() -> ResourceConfig:
    """The full action-framework opt-in: dump, permissions, mixed gates.

    Has both gated and ungated CRUD ops so the integration tests
    can assert each path independently:

    * ``get``, ``list``, ``update`` are gated.
    * ``create``, ``delete`` are not.
    """
    return ResourceConfig(
        model="blog.models.Post",
        include_actions_in_dump=True,
        permissions_endpoint=True,
        operations=[
            _read_op("get", can="blog.guards.can_get_post"),
            _read_op("list", can="blog.guards.can_list_post"),
            _write_op("create"),
            _write_op("update", can="blog.guards.can_update_post"),
            OperationConfig(name="delete"),
        ],
    )


@pytest.fixture
def files() -> dict[str, str]:
    """Run the full pipeline against an action-framework project."""
    config = ProjectConfig(
        auth=AuthConfig(
            credentials_schema="myapp.auth.LoginCredentials",
            session_schema="myapp.auth.Session",
            validate_fn="myapp.auth.validate",
        ),
        databases=[DatabaseConfig(key="primary", default=True)],
        apps=[
            App(
                config=AppConfig(module="blog", resources=[_post_resource()]),
                prefix="/blog",
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


def test_actions_registry_emitted(files: dict[str, str]) -> None:
    """The per-app registry holds object + collection tuples and
    binds the configured guards by their bare names."""
    actions = files["blog/actions.py"]
    assert "POST_OBJECT_ACTIONS = (" in actions
    assert "POST_COLLECTION_ACTIONS = (" in actions

    # Configured guards bind by their bare names.
    assert "from blog.guards import" in actions
    assert "can_get_post" in actions
    assert "can_list_post" in actions
    assert "can_update_post" in actions
    # Ungated ops fall back to always_true.
    assert "always_true" in actions


def test_resource_schema_includes_actions_field(files: dict[str, str]) -> None:
    """Both single-resource and list-item schemas dump actions."""
    schema = files["blog/schemas/post.py"]
    assert "from ingot.actions import ActionRef" in schema
    assert "class PostResource(BaseModel):" in schema
    assert "class PostListItem(BaseModel):" in schema
    # Actions field appears on both response shapes.
    assert schema.count("actions: list[ActionRef]") == 2


def test_serializer_threads_session_and_includes_actions(
    files: dict[str, str],
) -> None:
    """Serializers turn async, take session, fold action lists."""
    ser = files["blog/serializers/post.py"]
    assert "async def to_post_resource(" in ser
    assert "session: Session," in ser
    assert "from ingot.actions import available_actions" in ser
    assert (
        "from _generated.blog.actions import "
        "POST_COLLECTION_ACTIONS, POST_OBJECT_ACTIONS"
    ) in ser
    # Both object and collection refs are folded into the dump.
    assert "available_actions(obj, session, POST_OBJECT_ACTIONS)" in ser
    assert "available_actions(None, session, POST_COLLECTION_ACTIONS)" in ser


def test_get_handler_gates_execution(files: dict[str, str]) -> None:
    """Get with ``can`` raises 403 before serializing."""
    routes = files["blog/routes/post.py"]
    assert (
        'if not await find_can(POST_OBJECT_ACTIONS, "get")(obj, session):'
        in routes
    )
    assert 'raise HTTPException(status_code=403, detail="Forbidden")' in routes
    # Awaits the async serializer.
    assert "return await to_post_resource(obj, session)" in routes


def test_list_handler_filters_rows_via_can_list(
    files: dict[str, str],
) -> None:
    """The list handler runs ``can_list`` per row post-fetch."""
    routes = files["blog/routes/post.py"]

    # The import collector folds ingot.actions members onto a single
    # line in the rendered import block; just assert each name lands.
    for name in ("filter_visible", "find_can"):
        assert name in routes

    assert (
        "rows = await filter_visible(\n"
        "        rows,\n"
        "        session,\n"
        '        find_can(POST_COLLECTION_ACTIONS, "list"),\n'
        "    )"
    ) in routes
    # List items come back through the async serializer.
    assert "[await to_post_list_item(obj, session) for obj in rows]" in routes


def test_create_without_can_does_not_emit_gate(files: dict[str, str]) -> None:
    """Ungated ops compile down to the simple SQL path."""
    routes = files["blog/routes/post.py"]
    # Find the create handler block and assert no gate emitted.
    create_start = routes.index("async def create_post(")
    next_handler = routes.index("\n@router", create_start)
    create_block = routes[create_start:next_handler]
    assert 'find_can(POST_COLLECTION_ACTIONS, "create")' not in create_block
    assert "raise HTTPException(status_code=403" not in create_block


def test_update_with_can_prefetches_row(files: dict[str, str]) -> None:
    """Gated update reads the row before issuing the UPDATE."""
    routes = files["blog/routes/post.py"]
    update_start = routes.index("async def update_post(")
    next_handler = routes.index("\n@router", update_start)
    update_block = routes[update_start:next_handler]
    # Prefetch via select(...) appears before update(...).
    select_idx = update_block.index("select(Post)")
    update_idx = update_block.index("update(Post)")
    assert select_idx < update_idx
    # Gate sits between the prefetch and the mutation.
    assert (
        'find_can(POST_OBJECT_ACTIONS, "update")(obj, session)' in update_block
    )


def test_permissions_endpoints_emitted(files: dict[str, str]) -> None:
    """Both object and collection permissions endpoints exist."""
    routes = files["blog/routes/post.py"]
    assert '@router.get("/{id}/permissions"' in routes
    assert '@router.get("/permissions"' in routes
    # Both endpoints reference the same per-app registry constants
    # the dump path uses -- single source of truth.
    assert "available_actions(obj, session, POST_OBJECT_ACTIONS)" in routes
    assert "available_actions(None, session, POST_COLLECTION_ACTIONS)" in routes


def test_permissions_route_registers_before_pk_route(
    files: dict[str, str],
) -> None:
    """Static ``/permissions`` must precede dynamic ``/{pk}`` to win
    FastAPI's first-match routing."""
    routes = files["blog/routes/post.py"]
    perm_collection = routes.index('@router.get("/permissions"')
    perm_object = routes.index('@router.get("/{id}/permissions"')
    pk_route = routes.index('@router.get("/{id}"')
    assert perm_object < pk_route
    assert perm_collection < pk_route


def test_session_threaded_even_when_op_has_no_require_auth(
    files: dict[str, str],
) -> None:
    """Auth force-includes session for dump-enabled resources."""
    routes = files["blog/routes/post.py"]

    # Every CRUD handler signature carries the session dep.
    for fn in (
        "async def get_post(",
        "async def list_posts(",
        "async def create_post(",
        "async def update_post(",
        "async def delete_post(",
        "async def permissions_post_object(",
        "async def permissions_post_collection(",
    ):
        start = routes.index(fn)
        end = routes.index(") ->", start)
        signature = routes[start:end]
        assert "Annotated[Session, Depends(get_session)]" in signature, fn
