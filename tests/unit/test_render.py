"""Tests for the render registry and build store."""

from unittest.mock import MagicMock

import pytest

from foundry.render import BuildStore, Fragment, RenderCtx, RenderRegistry

# -------------------------------------------------------------------
# RenderRegistry
# -------------------------------------------------------------------


def _frag(tag: str) -> Fragment:
    """Build a Fragment whose shell_template encodes *tag* for assertions."""
    return Fragment(path="out.py", shell_template=tag)


def test_renders_decorator_registers():
    reg = RenderRegistry()

    @reg.renders(int)
    def render_int(_obj, _ctx):
        return _frag("int")

    assert reg.has_renderer(int)
    assert not reg.has_renderer(str)


def test_render_calls_registered_fn():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    @reg.renders(int)
    def render_int(obj, _ctx):
        return _frag(f"value={obj}")

    result = reg.render(42, ctx)
    assert len(result) == 1
    assert result[0].shell_template == "value=42"


def test_render_normalizes_list_return():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    @reg.renders(int)
    def render_int(_obj, _ctx):
        return [_frag("a"), _frag("b")]

    fragments = reg.render(1, ctx)
    assert [f.shell_template for f in fragments] == ["a", "b"]


def test_render_no_renderer_raises():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    with pytest.raises(LookupError, match="int"):
        reg.render(42, ctx)


# -------------------------------------------------------------------
# BuildStore
# -------------------------------------------------------------------


def test_store_add_and_get():
    store = BuildStore()
    store.add("resource", "user", "get", "handler1")
    result = store.get("resource", "user", "get")
    assert result == ["handler1"]


def test_store_get_empty():
    store = BuildStore()
    assert store.get("resource", "user", "get") == []


def test_store_add_multiple():
    store = BuildStore()
    store.add("resource", "user", "get", "a", "b")
    store.add("resource", "user", "get", "c")
    assert store.get("resource", "user", "get") == [
        "a",
        "b",
        "c",
    ]


def test_store_get_by_scope():
    store = BuildStore()
    store.add("resource", "user", "get", "h1")
    store.add("resource", "user", "list", "h2")
    store.add("resource", "post", "get", "h3")
    result = store.get_by_scope("resource", "user")
    assert set(result) == {"h1", "h2"}


def test_store_get_by_type():
    store = BuildStore()
    store.add("resource", "user", "get", 1, "a")
    store.add("resource", "user", "list", 2, "b")
    ints = store.get_by_type(int)
    assert set(ints) == {1, 2}


def test_store_all_items():
    store = BuildStore()
    store.add("resource", "user", "get", "a")
    store.add("app", "main", "router", "b")
    assert set(store.all_items()) == {"a", "b"}


def test_store_entries_iter():
    store = BuildStore()
    store.add("resource", "user", "get", "h1", "h2")
    store.add("project", "project", "scaffold", "sf")
    tuples = [
        (scope, instance_id, op_name, items)
        for scope, instance_id, op_name, items in store.entries()
    ]
    assert ("resource", "user", "get", ["h1", "h2"]) in tuples
    assert ("project", "project", "scaffold", ["sf"]) in tuples


def test_store_children_returns_registered_children_in_order():
    store = BuildStore()
    store.register_instance("app", "blog", "blog_app")
    store.register_instance("resource", "a", "A", parent=("app", "blog"))
    store.register_instance("resource", "b", "B", parent=("app", "blog"))

    children = store.children("app", "blog")

    assert children == [
        (("resource", "a"), "A"),
        (("resource", "b"), "B"),
    ]


def test_store_children_filters_by_scope():
    store = BuildStore()
    store.register_instance("app", "blog", "blog_app")
    store.register_instance("resource", "a", "A", parent=("app", "blog"))
    store.register_instance("database", "primary", "db", parent=("app", "blog"))

    resources = store.children("app", "blog", child_scope="resource")

    assert resources == [(("resource", "a"), "A")]


def test_store_children_dedupes_repeat_registration():
    """Registering the same instance twice doesn't duplicate the edge."""
    store = BuildStore()
    store.register_instance("app", "blog", "blog_app")
    store.register_instance("resource", "a", "A", parent=("app", "blog"))
    store.register_instance("resource", "a", "A", parent=("app", "blog"))

    assert store.children("app", "blog") == [(("resource", "a"), "A")]


def test_store_descendants_of_type_filters_and_returns_items():
    store = BuildStore()
    store.register_instance("app", "blog", "blog_app")
    store.register_instance("resource", "a", "A", parent=("app", "blog"))
    store.register_instance("resource", "b", "B", parent=("app", "blog"))
    store.add("resource", "a", "get", 1, "skip_me")
    store.add("resource", "b", "get", "skip_me_too")

    result = store.descendants_of_type(
        "app",
        "blog",
        int,
        child_scope="resource",
    )

    # Only resource "a" has an int output; "b" has only strings.
    assert result == [(("resource", "a"), "A", [1])]
