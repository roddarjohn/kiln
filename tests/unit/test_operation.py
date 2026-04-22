"""Tests for the operation protocol, decorator, and registry."""

import pytest
from pydantic import BaseModel

from foundry import EmptyOptions, OperationMeta, operation
from foundry.operation import OperationRegistry

# -------------------------------------------------------------------
# @operation decorator
# -------------------------------------------------------------------


def test_operation_decorator_registers_meta():
    registry = OperationRegistry()

    @operation("get", scope="resource", registry=registry)
    class Get:
        def build(self, _ctx, _options):
            return []

    assert len(registry.entries) == 1
    meta, cls = registry.entries[0]
    assert meta.name == "get"
    assert meta.scope == "resource"
    assert meta.requires == ()
    assert cls is Get


def test_operation_decorator_with_requires():
    registry = OperationRegistry()

    @operation(
        "router",
        scope="app",
        requires=["get", "list"],
        registry=registry,
    )
    class Router:
        def build(self, _ctx, _options):
            return []

    meta, _ = registry.entries[0]
    assert meta.requires == ("get", "list")


def test_operation_decorator_adds_empty_options():
    registry = OperationRegistry()

    @operation("scaffold", scope="project", registry=registry)
    class Scaffold:
        def build(self, _ctx, _options):
            return []

    assert Scaffold.Options is EmptyOptions


def test_operation_decorator_preserves_custom_options():
    registry = OperationRegistry()

    @operation("create", scope="resource", registry=registry)
    class Create:
        class Options(BaseModel):
            fields: list[str] | None = None

        def build(self, _ctx, _options):
            return []

    assert Create.Options is not EmptyOptions
    assert issubclass(Create.Options, BaseModel)


# -------------------------------------------------------------------
# OperationMeta
# -------------------------------------------------------------------


def test_operation_meta_frozen():
    meta = OperationMeta(name="get", scope="resource")
    with pytest.raises(AttributeError):
        meta.name = "list"


def test_operation_after_children_default_false():
    registry = OperationRegistry()

    @operation("get", scope="resource", registry=registry)
    class Get:
        def build(self, _ctx, _options):
            return []

    meta, _ = registry.entries[0]
    assert meta.after_children is False


def test_operation_after_children_flag():
    registry = OperationRegistry()

    @operation(
        "router",
        scope="project",
        after_children=True,
        registry=registry,
    )
    class Router:
        def build(self, _ctx, _options):
            return []

    meta, _ = registry.entries[0]
    assert meta.after_children is True


# -------------------------------------------------------------------
# OperationRegistry
# -------------------------------------------------------------------


def _names_for_scope(
    registry: OperationRegistry,
    scope: str,
) -> list[str]:
    """Names of the sorted entries in *scope*, for test assertions."""
    return [entry.meta.name for entry in registry.sorted_by_scope()[scope]]


def test_registry_sorted_by_scope_no_deps():
    registry = OperationRegistry()

    @operation("b", scope="resource", registry=registry)
    class B:
        def build(self, _ctx, _options):
            return []

    @operation("a", scope="resource", registry=registry)
    class A:
        def build(self, _ctx, _options):
            return []

    assert _names_for_scope(registry, "resource") == ["a", "b"]


def test_registry_sorted_by_scope_respects_deps():
    registry = OperationRegistry()

    @operation(
        "second", scope="resource", requires=["first"], registry=registry
    )
    class Second:
        def build(self, _ctx, _options):
            return []

    @operation("first", scope="resource", registry=registry)
    class First:
        def build(self, _ctx, _options):
            return []

    names = _names_for_scope(registry, "resource")
    assert names.index("first") < names.index("second")


def test_registry_sorted_by_scope_chain():
    registry = OperationRegistry()

    @operation("c", scope="resource", requires=["b"], registry=registry)
    class C:
        def build(self, _ctx, _options):
            return []

    @operation("a", scope="resource", registry=registry)
    class A:
        def build(self, _ctx, _options):
            return []

    @operation("b", scope="resource", requires=["a"], registry=registry)
    class B:
        def build(self, _ctx, _options):
            return []

    assert _names_for_scope(registry, "resource") == ["a", "b", "c"]


def test_registry_sorted_by_scope_missing_dep_raises():
    registry = OperationRegistry()

    @operation("x", scope="resource", requires=["missing"], registry=registry)
    class X:
        def build(self, _ctx, _options):
            return []

    with pytest.raises(ValueError, match="missing"):
        registry.sorted_by_scope()


def test_registry_sorted_by_scope_cycle_raises():
    registry = OperationRegistry()

    @operation("p", scope="resource", requires=["q"], registry=registry)
    class P:
        def build(self, _ctx, _options):
            return []

    @operation("q", scope="resource", requires=["p"], registry=registry)
    class Q:
        def build(self, _ctx, _options):
            return []

    with pytest.raises(ValueError, match="Cycle"):
        registry.sorted_by_scope()


def test_registry_validate_scopes_raises_for_unknown():
    registry = OperationRegistry()

    @operation("weird", scope="nonexistent", registry=registry)
    class W:
        def build(self, _ctx, _options):
            return []

    with pytest.raises(ValueError, match="nonexistent"):
        registry.validate_scopes({"project", "resource"})


def test_registry_sorted_by_scope_groups_per_scope():
    registry = OperationRegistry()

    @operation("r_op", scope="resource", registry=registry)
    class ResourceOp:
        def build(self, _ctx, _options):
            return []

    @operation("p_op", scope="project", registry=registry)
    class ProjectOp:
        def build(self, _ctx, _options):
            return []

    sorted_by_scope = registry.sorted_by_scope()

    assert [e.meta.name for e in sorted_by_scope["resource"]] == ["r_op"]
    assert [e.meta.name for e in sorted_by_scope["project"]] == ["p_op"]
