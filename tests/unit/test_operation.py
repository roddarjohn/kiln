"""Tests for the operation protocol and decorator."""

import pytest
from pydantic import BaseModel

from foundry import (
    EmptyOptions,
    OperationMeta,
    get_operation_meta,
    operation,
    topological_sort,
)

# -------------------------------------------------------------------
# @operation decorator
# -------------------------------------------------------------------


def test_operation_decorator_attaches_meta():
    @operation("get", scope="resource")
    class Get:
        def build(self, _ctx, _options):
            return []

    meta = get_operation_meta(Get)
    assert meta is not None
    assert meta.name == "get"
    assert meta.scope == "resource"
    assert meta.requires == ()


def test_operation_decorator_with_requires():
    @operation("router", scope="app", requires=["get", "list"])
    class Router:
        def build(self, _ctx, _options):
            return []

    meta = get_operation_meta(Router)
    assert meta.requires == ("get", "list")


def test_operation_decorator_adds_empty_options():
    @operation("scaffold", scope="project")
    class Scaffold:
        def build(self, _ctx, _options):
            return []

    assert Scaffold.Options is EmptyOptions


def test_operation_decorator_preserves_custom_options():
    @operation("create", scope="resource")
    class Create:
        class Options(BaseModel):
            fields: list[str] | None = None

        def build(self, _ctx, _options):
            return []

    assert Create.Options is not EmptyOptions
    assert issubclass(Create.Options, BaseModel)


def test_get_operation_meta_returns_none_for_plain_class():
    class Plain:
        pass

    assert get_operation_meta(Plain) is None


# -------------------------------------------------------------------
# OperationMeta
# -------------------------------------------------------------------


def test_operation_meta_frozen():
    meta = OperationMeta(name="get", scope="resource")
    with pytest.raises(AttributeError):
        meta.name = "list"


def test_operation_after_children_default_false():
    @operation("get", scope="resource")
    class Get:
        def build(self, _ctx, _options):
            return []

    meta = get_operation_meta(Get)
    assert meta.after_children is False


def test_operation_after_children_flag():
    @operation("router", scope="project", after_children=True)
    class Router:
        def build(self, _ctx, _options):
            return []

    meta = get_operation_meta(Router)
    assert meta.after_children is True


# -------------------------------------------------------------------
# Topological sort
# -------------------------------------------------------------------


def test_topo_sort_no_deps():
    @operation("a", scope="resource")
    class A:
        def build(self, _ctx, _options):
            return []

    @operation("b", scope="resource")
    class B:
        def build(self, _ctx, _options):
            return []

    result = topological_sort([B, A])
    names = [get_operation_meta(c).name for c in result]
    assert "a" in names
    assert "b" in names


def test_topo_sort_respects_deps():
    @operation("first", scope="resource")
    class First:
        def build(self, _ctx, _options):
            return []

    @operation("second", scope="resource", requires=["first"])
    class Second:
        def build(self, _ctx, _options):
            return []

    result = topological_sort([Second, First])
    names = [get_operation_meta(c).name for c in result]
    assert names.index("first") < names.index("second")


def test_topo_sort_chain():
    @operation("a", scope="resource")
    class A:
        def build(self, _ctx, _options):
            return []

    @operation("b", scope="resource", requires=["a"])
    class B:
        def build(self, _ctx, _options):
            return []

    @operation("c", scope="resource", requires=["b"])
    class C:
        def build(self, _ctx, _options):
            return []

    result = topological_sort([C, A, B])
    names = [get_operation_meta(c).name for c in result]
    assert names == ["a", "b", "c"]


def test_topo_sort_missing_dep_raises():
    @operation("x", scope="resource", requires=["missing"])
    class X:
        def build(self, _ctx, _options):
            return []

    with pytest.raises(ValueError, match="missing"):
        topological_sort([X])


def test_topo_sort_cycle_raises():
    @operation("p", scope="resource", requires=["q"])
    class P:
        def build(self, _ctx, _options):
            return []

    @operation("q", scope="resource", requires=["p"])
    class Q:
        def build(self, _ctx, _options):
            return []

    with pytest.raises(ValueError, match="Cycle"):
        topological_sort([P, Q])


def test_topo_sort_no_meta_raises():
    class NoMeta:
        pass

    with pytest.raises(ValueError, match="no @operation"):
        topological_sort([NoMeta])
