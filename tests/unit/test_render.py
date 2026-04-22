"""Tests for the render registry and build store."""

from unittest.mock import MagicMock

import pytest

from foundry.render import FileFragment, RenderCtx, RenderRegistry
from foundry.scope import PROJECT, Scope, ScopeTree
from foundry.store import BuildStore

# -------------------------------------------------------------------
# RenderRegistry
# -------------------------------------------------------------------


def _frag(tag: str) -> FileFragment:
    """Build a FileFragment whose template encodes *tag* for assertions."""
    return FileFragment(path="out.py", template=tag)


def test_renders_decorator_registers():
    reg = RenderRegistry()

    @reg.renders(int)
    def render_int(_obj, _ctx):
        yield _frag("int")

    assert int in reg._entries
    assert str not in reg._entries


def test_render_calls_registered_fn():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    @reg.renders(int)
    def render_int(obj, _ctx):
        yield _frag(f"value={obj}")

    result = reg.render(42, ctx)
    assert len(result) == 1
    assert result[0].template == "value=42"


def test_render_accepts_list_return():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    @reg.renders(int)
    def render_int(_obj, _ctx):
        return [_frag("a"), _frag("b")]

    fragments = reg.render(1, ctx)
    assert [f.template for f in fragments] == ["a", "b"]


def test_render_no_renderer_raises():
    reg = RenderRegistry()
    env = MagicMock()
    ctx = RenderCtx(env=env, config={})

    with pytest.raises(LookupError, match="int"):
        reg.render(42, ctx)


# -------------------------------------------------------------------
# BuildStore
# -------------------------------------------------------------------


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


def test_store_outputs_under_filters_by_type():
    store = BuildStore(scope_tree=_SCOPE_TREE)
    app_id = "project.apps.0"
    res_a = f"{app_id}.resources.0"
    res_b = f"{app_id}.resources.1"
    store.register_instance(app_id, "blog_app")
    store.register_instance(res_a, "A", parent=app_id)
    store.register_instance(res_b, "B", parent=app_id)
    store.add(res_a, "get", 1, "skip_me")
    store.add(res_b, "get", "skip_me_too")

    # Only ``1`` is an int anywhere under app_id.
    assert store.outputs_under(app_id, int) == [1]
