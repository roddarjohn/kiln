"""Tests for the build engine."""

from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from be.operations.types import RouteHandler
from foundry import Engine, Scoped, operation
from foundry.engine import _resolve_options
from foundry.operation import EmptyOptions, OperationRegistry
from foundry.outputs import StaticFile

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

    opts = _resolve_options(Op, object())
    assert isinstance(opts, EmptyOptions)


def test_resolve_options_custom():
    class Op:
        class Options(BaseModel):
            count: int = 5

    opts = _resolve_options(Op, object())
    assert opts.count == 5


def test_resolve_options_from_instance():
    class Op:
        class Options(BaseModel):
            count: int = 5

    class Inst(BaseModel):
        options: dict = {"count": 10}

    opts = _resolve_options(Op, Inst())
    assert opts.count == 10


# -------------------------------------------------------------------
# Engine.build
# -------------------------------------------------------------------


def test_engine_build_project_scope():
    registry = OperationRegistry()
    _register_scaffold(registry)

    store = Engine(registry=registry).build(ProjectConfig())
    items = store.outputs_under("project", StaticFile)
    assert len(items) == 1


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
    handlers = store.outputs_under("project", RouteHandler)
    names = {h.function_name for h in handlers}
    assert names == {"get_user", "get_post"}


def test_engine_build_multiple_scopes():
    registry = OperationRegistry()
    _register_scaffold(registry)
    _register_get(registry)
    config = ProjectConfig(resources=[ResourceConfig(name="user")])

    store = Engine(registry=registry).build(config)
    assert len(store.outputs_under("project", StaticFile)) == 1
    assert len(store.outputs_under("project", RouteHandler)) == 1


def test_engine_empty_operations():
    engine = Engine(registry=OperationRegistry())
    config = ProjectConfig()
    store = engine.build(config)
    assert list(store.entries()) == []


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
            earlier = ctx.store.outputs_under(ctx.instance_id, RouteHandler)
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
    assert list(store.entries()) == []


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
    assert len(store.outputs_under("project", StaticFile)) == 1
    assert len(store.outputs_under("project", RouteHandler)) == 1


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
            handlers = ctx.store.outputs_under("project", RouteHandler)
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

    agg_items = store.outputs_under("project", StaticFile)
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


def test_engine_dispatches_by_name():
    """``dispatch_on="name"`` runs each op only on the matching instance."""

    class Entry(BaseModel):
        name: str

    class Host(BaseModel):
        entries: Annotated[list[Entry], Scoped(name="entry")] = Field(
            default_factory=list,
        )

    class Cfg(BaseModel):
        hosts: Annotated[list[Host], Scoped(name="host")] = Field(
            default_factory=list,
        )

    registry = OperationRegistry()

    @operation("alpha", scope="entry", dispatch_on="name", registry=registry)
    class Alpha:
        def build(self, _ctx, _options):
            return [RouteHandler(method="GET", path="/", function_name="alpha")]

    @operation("beta", scope="entry", dispatch_on="name", registry=registry)
    class Beta:
        def build(self, _ctx, _options):
            return [RouteHandler(method="GET", path="/", function_name="beta")]

    config = Cfg(
        hosts=[Host(entries=[Entry(name="alpha"), Entry(name="beta")])]
    )
    store = Engine(registry=registry).build(config)

    handlers = store.outputs_under("project", RouteHandler)
    names = {h.function_name for h in handlers}
    assert names == {"alpha", "beta"}
    # Each op fires exactly once, on the entry whose name matches it.
    assert len(store.outputs_under("project", RouteHandler)) == 2
