"""Tests for the render registry and build store."""

from unittest.mock import MagicMock

import pytest

from foundry.render import BuildStore, Fragment, RenderCtx, RenderRegistry
from foundry.scope import PROJECT, Scope, ScopeTree

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
    store.add("user", "get", "handler1")
    assert store.get("user", "get") == ["handler1"]


def test_store_get_empty():
    store = BuildStore()
    assert store.get("user", "get") == []


def test_store_add_multiple():
    store = BuildStore()
    store.add("user", "get", "a", "b")
    store.add("user", "get", "c")
    assert store.get("user", "get") == ["a", "b", "c"]


def test_store_get_by_instance():
    store = BuildStore()
    store.add("user", "get", "h1")
    store.add("user", "list", "h2")
    store.add("post", "get", "h3")
    assert set(store.get_by_instance("user")) == {"h1", "h2"}


def test_store_get_by_type():
    store = BuildStore()
    store.add("user", "get", 1, "a")
    store.add("user", "list", 2, "b")
    ints = store.get_by_type(int)
    assert set(ints) == {1, 2}


def test_store_all_items():
    store = BuildStore()
    store.add("user", "get", "a")
    store.add("main", "router", "b")
    assert set(store.all_items()) == {"a", "b"}


def test_store_entries_iter():
    store = BuildStore()
    store.add("user", "get", "h1", "h2")
    store.add("project", "scaffold", "sf")
    tuples = [
        (instance_id, op_name, items)
        for instance_id, op_name, items in store.entries()
    ]
    assert ("user", "get", ["h1", "h2"]) in tuples
    assert ("project", "scaffold", ["sf"]) in tuples


# Scope tree used by the store-ancestry tests below.  Mirrors
# what ``discover_scopes`` would produce for a config with
# ``apps[*].resources`` and ``apps[*].databases``.
_APP_SCOPE = Scope(name="app", config_key="apps", parent=PROJECT)
_RESOURCE_SCOPE = Scope(
    name="resource", config_key="resources", parent=_APP_SCOPE
)
_DATABASE_SCOPE = Scope(
    name="database", config_key="databases", parent=_APP_SCOPE
)
_SCOPE_TREE = ScopeTree([PROJECT, _APP_SCOPE, _RESOURCE_SCOPE, _DATABASE_SCOPE])


def test_store_children_returns_registered_children_in_order():
    store = BuildStore(scope_tree=_SCOPE_TREE)
    app_id = "project.apps.0"
    store.register_instance(app_id, "blog_app")
    store.register_instance(
        f"{app_id}.resources.0",
        "A",
        parent=app_id,
    )
    store.register_instance(
        f"{app_id}.resources.1",
        "B",
        parent=app_id,
    )

    assert store.children(app_id) == [
        (f"{app_id}.resources.0", "A"),
        (f"{app_id}.resources.1", "B"),
    ]


def test_store_children_filters_by_scope():
    store = BuildStore(scope_tree=_SCOPE_TREE)
    app_id = "project.apps.0"
    store.register_instance(app_id, "blog_app")
    store.register_instance(
        f"{app_id}.resources.0",
        "A",
        parent=app_id,
    )
    store.register_instance(
        f"{app_id}.databases.0",
        "db",
        parent=app_id,
    )

    resources = store.children(app_id, child_scope="resource")
    assert resources == [(f"{app_id}.resources.0", "A")]


def test_store_children_dedupes_repeat_registration():
    """Registering the same instance twice doesn't duplicate the edge."""
    store = BuildStore(scope_tree=_SCOPE_TREE)
    app_id = "project.apps.0"
    store.register_instance(app_id, "blog_app")
    store.register_instance(
        f"{app_id}.resources.0",
        "A",
        parent=app_id,
    )
    store.register_instance(
        f"{app_id}.resources.0",
        "A",
        parent=app_id,
    )

    assert store.children(app_id) == [(f"{app_id}.resources.0", "A")]


def test_store_descendants_of_type_filters_and_returns_items():
    store = BuildStore(scope_tree=_SCOPE_TREE)
    app_id = "project.apps.0"
    res_a = f"{app_id}.resources.0"
    res_b = f"{app_id}.resources.1"
    store.register_instance(app_id, "blog_app")
    store.register_instance(res_a, "A", parent=app_id)
    store.register_instance(res_b, "B", parent=app_id)
    store.add(res_a, "get", 1, "skip_me")
    store.add(res_b, "get", "skip_me_too")

    result = store.descendants_of_type(
        app_id,
        int,
        child_scope="resource",
    )

    # Only resource A has an int output; B has only strings.
    assert result == [(res_a, "A", [1])]
