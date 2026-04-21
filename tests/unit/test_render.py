"""Tests for the render registry and IR store."""

from unittest.mock import MagicMock

import pytest

from foundry.render import BuildStore, RenderCtx, RenderRegistry

# -------------------------------------------------------------------
# RenderRegistry
# -------------------------------------------------------------------


def test_renders_decorator_registers():
    reg = RenderRegistry()

    @reg.renders(int)
    def render_int(_obj, _ctx):
        return "int"

    assert reg.has_renderer(int)
    assert not reg.has_renderer(str)


def test_render_calls_registered_fn():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    @reg.renders(int)
    def render_int(obj, _ctx):
        return f"value={obj}"

    assert reg.render(42, ctx) == "value=42"


def test_render_no_renderer_raises():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    with pytest.raises(LookupError, match="int"):
        reg.render(42, ctx)


def test_render_when_predicate_selects():
    reg = RenderRegistry()
    env = MagicMock()

    @reg.renders(int, when=lambda cfg: cfg.get("fast"))
    def render_fast(_obj, _ctx):
        return "fast"

    @reg.renders(int)
    def render_default(_obj, _ctx):
        return "default"

    fast_ctx = RenderCtx(env=env, config={"fast": True})
    assert reg.render(1, fast_ctx) == "fast"

    slow_ctx = RenderCtx(env=env, config={})
    assert reg.render(1, slow_ctx) == "default"


def test_render_when_all_fail_raises():
    reg = RenderRegistry()
    env = MagicMock()

    @reg.renders(int, when=lambda _cfg: False)
    def render_never(_obj, _ctx):
        return "never"

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
