"""Tests for the build engine."""

from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from foundry import Engine, Scoped, operation
from foundry.engine import (
    _allowed_ops,
    _find_op_options,
    _instance_id,
    _resolve_options,
)
from foundry.operation import EmptyOptions
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
# Test operations
# -------------------------------------------------------------------


@operation("scaffold", scope="project")
class Scaffold:
    def build(self, _ctx, _options):
        return [StaticFile(path="main.py", template="main.j2")]


@operation("get", scope="resource")
class Get:
    def build(self, ctx, _options):
        name = ctx.instance.name
        return [
            RouteHandler(
                method="GET",
                path=f"/{name}/{{id}}",
                function_name=f"get_{name}",
            )
        ]


@operation("list", scope="resource")
class List:
    def build(self, ctx, _options):
        name = ctx.instance.name
        return [
            RouteHandler(
                method="GET",
                path=f"/{name}",
                function_name=f"list_{name}",
            )
        ]


@operation("router", scope="app", requires=["get", "list"])
class AppRouter:
    def build(self, ctx, _options):
        return [
            StaticFile(
                path=f"{ctx.instance.name}/router.py",
                template="router.j2",
            )
        ]


# -------------------------------------------------------------------
# _resolve_options
# -------------------------------------------------------------------


def test_resolve_options_default():
    @operation("test_op", scope="resource")
    class Op:
        def build(self, _ctx, _options):
            return []

    opts = _resolve_options(Op, object())
    assert isinstance(opts, EmptyOptions)


def test_resolve_options_custom():
    @operation("test_op2", scope="resource")
    class Op:
        class Options(BaseModel):
            count: int = 5

        def build(self, _ctx, _options):
            return []

    opts = _resolve_options(Op, object())
    assert opts.count == 5


def test_resolve_options_from_instance():
    @operation("test_op3", scope="resource")
    class Op:
        class Options(BaseModel):
            count: int = 5

        def build(self, _ctx, _options):
            return []

    class Inst(BaseModel):
        options: dict = {"count": 10}

    opts = _resolve_options(Op, Inst())
    assert opts.count == 10


# -------------------------------------------------------------------
# Engine.build
# -------------------------------------------------------------------


def test_engine_build_project_scope():
    engine = Engine(operations=[Scaffold])
    config = ProjectConfig()
    store = engine.build(config)
    items = store.get("project", "project", "scaffold")
    assert len(items) == 1
    assert isinstance(items[0], StaticFile)


def test_engine_build_resource_scope():
    config = ProjectConfig(
        resources=[
            ResourceConfig(name="user"),
            ResourceConfig(name="post"),
        ]
    )
    engine = Engine(operations=[Get])
    store = engine.build(config)

    user_items = store.get("resource", "user", "get")
    assert len(user_items) == 1
    assert user_items[0].function_name == "get_user"

    post_items = store.get("resource", "post", "get")
    assert len(post_items) == 1
    assert post_items[0].function_name == "get_post"


def test_engine_build_multiple_scopes():
    config = ProjectConfig(resources=[ResourceConfig(name="user")])
    engine = Engine(operations=[Scaffold, Get])
    store = engine.build(config)

    assert len(store.get("project", "project", "scaffold")) == 1
    assert len(store.get("resource", "user", "get")) == 1


def test_engine_empty_operations():
    engine = Engine(operations=[])
    config = ProjectConfig()
    store = engine.build(config)
    assert store.all_items() == []


def test_engine_unknown_scope_raises():
    @operation("bad_op", scope="nonexistent")
    class Bad:
        def build(self, _ctx, _options):
            return []

    engine = Engine(operations=[Bad])
    config = ProjectConfig()
    with pytest.raises(ValueError, match="nonexistent"):
        engine.build(config)


def test_engine_no_meta_raises():
    class Plain:
        pass

    engine = Engine(operations=[Plain])
    config = ProjectConfig()
    with pytest.raises(ValueError, match="no @operation"):
        engine.build(config)


def test_engine_respects_dependency_order():
    """Operations run in topo order within a scope."""
    call_order: list[str] = []

    @operation("first", scope="resource")
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

    @operation("second", scope="resource", requires=["first"])
    class Second:
        def build(self, ctx, _options):
            call_order.append("second")
            # Can see first's output in the store
            earlier = ctx.store.get("resource", ctx.instance_id, "first")
            assert len(earlier) == 1
            return []

    config = ProjectConfig(resources=[ResourceConfig(name="user")])
    engine = Engine(operations=[Second, First])
    engine.build(config)
    assert call_order == ["first", "second"]


def test_engine_build_returns_empty_for_empty_scope():
    """No items in a scope means no operations run."""
    config = ProjectConfig(resources=[])
    engine = Engine(operations=[Get])
    store = engine.build(config)
    assert store.all_items() == []


def test_engine_auto_discovers_scopes():
    engine = Engine(operations=[Scaffold])
    config = ProjectConfig()
    engine.build(config)
    scope_names = [s.name for s in engine.scopes]
    assert "project" in scope_names
    assert "app" in scope_names
    assert "resource" in scope_names


# -------------------------------------------------------------------
# _instance_id
# -------------------------------------------------------------------


def test_instance_id_from_name():
    class Item(BaseModel):
        name: str

    assert _instance_id(Item(name="user"), "resource", 0) == "user"


def test_instance_id_from_model():
    class Item(BaseModel):
        model: str

    result = _instance_id(Item(model="myapp.models.Article"), "resource", 0)
    assert result == "article"


def test_instance_id_fallback():
    class Item(BaseModel):
        value: int

    assert _instance_id(Item(value=1), "thing", 3) == "thing_3"


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

    @operation("child_op", scope="resource")
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

    @operation("aggregator", scope="project", after_children=True)
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
    engine = Engine(operations=[Aggregator, ChildOp])
    store = engine.build(config)

    # Aggregator ran after every resource, not before them.
    assert call_order[-1] == "aggregator"
    assert {"child:user", "child:post"}.issubset(call_order[:-1])

    agg_items = store.get("project", "project", "aggregator")
    assert len(agg_items) == 1
    assert agg_items[0].context["handlers"] == ["handler_post", "handler_user"]


def test_engine_after_children_at_any_scope():
    """``after_children=True`` runs at each instance after its children.

    For a leaf scope (no children) it still runs, after the
    instance's pre-phase ops.
    """
    call_order: list[str] = []

    @operation("pre_resource", scope="resource")
    class Pre:
        def build(self, ctx, _options):
            call_order.append(f"pre:{ctx.instance.name}")
            return []

    @operation("post_resource", scope="resource", after_children=True)
    class Post:
        def build(self, ctx, _options):
            call_order.append(f"post:{ctx.instance.name}")
            return []

    config = ProjectConfig(resources=[ResourceConfig(name="x")])
    engine = Engine(operations=[Pre, Post])
    engine.build(config)
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
    engine = Engine(operations=[Get, List])
    store = engine.build(config)

    # get should run
    assert len(store.get("resource", "user", "get")) == 1
    # list should NOT run
    assert len(store.get("resource", "user", "list")) == 0
