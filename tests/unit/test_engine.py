"""Tests for the build engine."""

from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from foundry import Engine, Scoped, operation
from foundry.engine import (
    _allowed_ops,
    _find_op_options,
    _resolve_options,
)
from foundry.operation import EmptyOptions, OperationMeta, OperationRegistry
from foundry.outputs import RouteHandler, StaticFile

# -------------------------------------------------------------------
# Test config models
# -------------------------------------------------------------------


class ResourceConfig(BaseModel):
    name: str


class AppConfig(BaseModel):
    name: str
    resources: Annotated[list[ResourceConfig], Scoped(name="resource")] = Field(
        default_factory=list
    )


class ProjectConfig(BaseModel):
    module: str = "myapp"
    apps: Annotated[list[AppConfig], Scoped(name="app")] = Field(
        default_factory=list,
    )
    resources: Annotated[list[ResourceConfig], Scoped(name="resource")] = Field(
        default_factory=list
    )


# -------------------------------------------------------------------
# Test-op builders
#
# Each test constructs an isolated OperationRegistry and registers
# just the ops it cares about — keeps ops from leaking across tests
# and from bleeding into the process-wide default registry.
# -------------------------------------------------------------------


def _register_scaffold(registry: OperationRegistry) -> None:
    @operation("scaffold", scope="project", registry=registry)
    class _Scaffold:
        def build(self, _ctx, _options):
            return [StaticFile(path="main.py", template="main.j2")]


def _register_get(registry: OperationRegistry) -> None:
    @operation("get", scope="resource", registry=registry)
    class _Get:
        def build(self, ctx, _options):
            name = ctx.instance.name
            return [
                RouteHandler(
                    method="GET",
                    path=f"/{name}/{{id}}",
                    function_name=f"get_{name}",
                )
            ]


def _register_list(registry: OperationRegistry) -> None:
    @operation("list", scope="resource", registry=registry)
    class _List:
        def build(self, ctx, _options):
            name = ctx.instance.name
            return [
                RouteHandler(
                    method="GET",
                    path=f"/{name}",
                    function_name=f"list_{name}",
                )
            ]


# -------------------------------------------------------------------
# _resolve_options
# -------------------------------------------------------------------


def test_resolve_options_default():
    class Op:
        pass

    meta = OperationMeta(name="test_op", scope="resource")
    opts = _resolve_options(meta, Op, object())
    assert isinstance(opts, EmptyOptions)


def test_resolve_options_custom():
    class Op:
        class Options(BaseModel):
            count: int = 5

    meta = OperationMeta(name="test_op2", scope="resource")
    opts = _resolve_options(meta, Op, object())
    assert opts.count == 5


def test_resolve_options_from_instance():
    class Op:
        class Options(BaseModel):
            count: int = 5

    class Inst(BaseModel):
        options: dict = {"count": 10}

    meta = OperationMeta(name="test_op3", scope="resource")
    opts = _resolve_options(meta, Op, Inst())
    assert opts.count == 10


# -------------------------------------------------------------------
# Engine.build
# -------------------------------------------------------------------


def test_engine_build_project_scope():
    registry = OperationRegistry()
    _register_scaffold(registry)

    store = Engine(registry=registry).build(ProjectConfig())
    items = store.get("project", "scaffold")
    assert len(items) == 1
    assert isinstance(items[0], StaticFile)


def test_engine_build_resource_scope():
    registry = OperationRegistry()
    _register_get(registry)
    config = ProjectConfig(
        resources=[
            ResourceConfig(name="user"),
            ResourceConfig(name="post"),
        ]
    )

    store = Engine(registry=registry).build(config)
    handlers = store.get_by_type(RouteHandler)
    names = {h.function_name for h in handlers}
    assert names == {"get_user", "get_post"}


def test_engine_build_multiple_scopes():
    registry = OperationRegistry()
    _register_scaffold(registry)
    _register_get(registry)
    config = ProjectConfig(resources=[ResourceConfig(name="user")])

    store = Engine(registry=registry).build(config)
    assert len(store.get_by_type(StaticFile)) == 1
    assert len(store.get_by_type(RouteHandler)) == 1


def test_engine_empty_operations():
    engine = Engine(registry=OperationRegistry())
    config = ProjectConfig()
    store = engine.build(config)
    assert store.all_items() == []


def test_engine_unknown_scope_raises():
    registry = OperationRegistry()

    @operation("bad_op", scope="nonexistent", registry=registry)
    class Bad:
        def build(self, _ctx, _options):
            return []

    engine = Engine(registry=registry)
    config = ProjectConfig()
    with pytest.raises(ValueError, match="nonexistent"):
        engine.build(config)


def test_engine_respects_dependency_order():
    """Operations run in topo order within a scope."""
    call_order: list[str] = []
    registry = OperationRegistry()

    @operation("first", scope="resource", registry=registry)
    class First:
        def build(self, _ctx, _options):
            call_order.append("first")
            return [
                RouteHandler(
                    method="GET",
                    path="/",
                    function_name="f",
                )
            ]

    @operation(
        "second",
        scope="resource",
        requires=["first"],
        registry=registry,
    )
    class Second:
        def build(self, ctx, _options):
            call_order.append("second")
            # Can see first's output in the store.
            earlier = ctx.store.get(ctx.instance_id, "first")
            assert len(earlier) == 1
            return []

    config = ProjectConfig(resources=[ResourceConfig(name="user")])
    Engine(registry=registry).build(config)
    assert call_order == ["first", "second"]


def test_engine_build_returns_empty_for_empty_scope():
    """No items in a scope means no operations run."""
    registry = OperationRegistry()
    _register_get(registry)

    store = Engine(registry=registry).build(ProjectConfig(resources=[]))
    assert store.all_items() == []


def test_engine_build_accepts_ops_across_all_discovered_scopes():
    """Engine discovers scopes from the config so ops at every level
    (project, resource) are accepted without an explicit list."""
    registry = OperationRegistry()
    _register_scaffold(registry)
    _register_get(registry)
    config = ProjectConfig(
        apps=[AppConfig(name="blog", resources=[ResourceConfig(name="post")])],
    )

    # Builds without validate_scopes raising, proving "project" and
    # "resource" scopes were both discovered from the config tree.
    store = Engine(registry=registry).build(config)
    assert len(store.get_by_type(StaticFile)) == 1
    assert len(store.get_by_type(RouteHandler)) == 1


# -------------------------------------------------------------------
# _allowed_ops
# -------------------------------------------------------------------


def test_allowed_ops_none_when_no_operations():
    class Item(BaseModel):
        name: str

    assert _allowed_ops(Item(name="x")) is None


def test_allowed_ops_from_string_list():
    class Item(BaseModel):
        operations: list[str] = []

    result = _allowed_ops(Item(operations=["get", "list"]))
    assert result == {"get", "list"}


def test_allowed_ops_from_objects():
    class OpEntry(BaseModel):
        name: str

    class Item(BaseModel):
        operations: list[str | OpEntry] = []

    result = _allowed_ops(Item(operations=["get", OpEntry(name="create")]))
    assert result == {"get", "create"}


# -------------------------------------------------------------------
# _find_op_options
# -------------------------------------------------------------------


def test_find_op_options_found():
    class OpEntry(BaseModel):
        name: str

        @property
        def options(self):
            return {"count": 10}

    class Item(BaseModel):
        operations: list[str | OpEntry] = []

    item = Item(operations=["get", OpEntry(name="create")])
    result = _find_op_options(item, "create")
    assert result == {"count": 10}


def test_find_op_options_not_found():
    class Item(BaseModel):
        operations: list[str] = []

    item = Item(operations=["get", "list"])
    assert _find_op_options(item, "create") is None


def test_find_op_options_no_operations():
    class Item(BaseModel):
        name: str

    assert _find_op_options(Item(name="x"), "get") is None


# -------------------------------------------------------------------
# Engine: operation filtering
# -------------------------------------------------------------------


def test_engine_after_children_sees_child_output():
    """after_children=True defers a project op until child scopes run."""
    call_order: list[str] = []
    registry = OperationRegistry()

    @operation("child_op", scope="resource", registry=registry)
    class ChildOp:
        def build(self, ctx, _options):
            call_order.append(f"child:{ctx.instance.name}")
            return [
                RouteHandler(
                    method="GET",
                    path="/",
                    function_name=f"handler_{ctx.instance.name}",
                )
            ]

    @operation(
        "aggregator",
        scope="project",
        after_children=True,
        registry=registry,
    )
    class Aggregator:
        def build(self, ctx, _options):
            call_order.append("aggregator")
            handlers = ctx.store.get_by_type(RouteHandler)
            names = sorted(h.function_name for h in handlers)
            return [
                StaticFile(
                    path="routes.py",
                    template="routes.j2",
                    context={"handlers": names},
                )
            ]

    config = ProjectConfig(
        resources=[
            ResourceConfig(name="user"),
            ResourceConfig(name="post"),
        ]
    )
    store = Engine(registry=registry).build(config)

    # Aggregator ran after every resource, not before them.
    assert call_order[-1] == "aggregator"
    assert {"child:user", "child:post"}.issubset(call_order[:-1])

    agg_items = store.get("project", "aggregator")
    assert len(agg_items) == 1
    assert agg_items[0].context["handlers"] == ["handler_post", "handler_user"]


def test_engine_after_children_at_any_scope():
    """``after_children=True`` runs at each instance after its children.

    For a leaf scope (no children) it still runs, after the
    instance's pre-phase ops.
    """
    call_order: list[str] = []
    registry = OperationRegistry()

    @operation("pre_resource", scope="resource", registry=registry)
    class Pre:
        def build(self, ctx, _options):
            call_order.append(f"pre:{ctx.instance.name}")
            return []

    @operation(
        "post_resource",
        scope="resource",
        after_children=True,
        registry=registry,
    )
    class Post:
        def build(self, ctx, _options):
            call_order.append(f"post:{ctx.instance.name}")
            return []

    config = ProjectConfig(resources=[ResourceConfig(name="x")])
    Engine(registry=registry).build(config)
    assert call_order == ["pre:x", "post:x"]


def test_engine_filters_by_allowed_ops():
    """Only operations in the instance's list run."""

    class FilterResource(BaseModel):
        name: str
        operations: list[str] = Field(default_factory=list)

    class FilterConfig(BaseModel):
        resources: Annotated[list[FilterResource], Scoped(name="resource")] = (
            Field(default_factory=list)
        )

    config = FilterConfig(
        resources=[
            FilterResource(
                name="user",
                operations=["get"],
            )
        ]
    )
    registry = OperationRegistry()
    _register_get(registry)
    _register_list(registry)

    store = Engine(registry=registry).build(config)

    get_handlers = [
        item
        for _iid, op, items in store.entries()
        if op == "get"
        for item in items
    ]
    list_handlers = [
        item
        for _iid, op, items in store.entries()
        if op == "list"
        for item in items
    ]
    assert len(get_handlers) == 1
    assert len(list_handlers) == 0
