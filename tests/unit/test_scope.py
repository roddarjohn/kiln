"""Tests for scope discovery."""

from pydantic import BaseModel

from foundry import PROJECT, Scope, discover_scopes


class ItemConfig(BaseModel):
    name: str


class SubConfig(BaseModel):
    items: list[ItemConfig] = []


class TopConfig(BaseModel):
    module: str = "app"
    things: list[SubConfig] = []
    items: list[ItemConfig] = []
    tags: list[str] = []


def test_project_scope_always_first():
    scopes = discover_scopes(TopConfig)
    assert scopes[0] is PROJECT
    assert scopes[0].name == "project"
    assert scopes[0].config_key == ""


def test_discovers_list_of_basemodel_fields():
    scopes = discover_scopes(TopConfig)
    names = [s.name for s in scopes]
    assert "thing" in names
    assert "item" in names


def test_skips_list_of_non_basemodel():
    scopes = discover_scopes(TopConfig)
    names = [s.name for s in scopes]
    assert "tag" not in names


def test_scope_config_key():
    scopes = discover_scopes(TopConfig)
    by_name = {s.name: s for s in scopes}
    assert by_name["thing"].config_key == "things"
    assert by_name["item"].config_key == "items"


def test_scope_tree_structure():
    """``item`` appears at two places in TopConfig: nested and direct.

    ``TopConfig.things[].items`` produces one ``item`` scope with
    ``parent=thing``; ``TopConfig.items`` produces a second with
    ``parent=PROJECT``.  Both coexist — ops dispatch by scope name
    and run at every node.
    """
    scopes = discover_scopes(TopConfig)
    thing = next(s for s in scopes if s.name == "thing")
    items = [s for s in scopes if s.name == "item"]

    assert thing.parent is PROJECT
    parents = {s.parent for s in items}
    assert PROJECT in parents
    assert thing in parents


def test_nested_scope_resolve_path_is_field_name_only():
    """A child scope resolves from its *parent scope instance*.

    So resolve_path carries just the field names needed to reach
    the list from the parent scope's instance — not from the root.
    """
    scopes = discover_scopes(TopConfig)
    thing = next(s for s in scopes if s.name == "thing")
    assert thing.resolve_path == ("things",)
    # Nested item: starts from a SubConfig instance, so path is ("items",).
    nested = next(s for s in scopes if s.name == "item" and s.parent is thing)
    assert nested.resolve_path == ("items",)


def test_empty_model():
    class Empty(BaseModel):
        pass

    scopes = discover_scopes(Empty)
    assert len(scopes) == 1
    assert scopes[0] is PROJECT


def test_scope_frozen():
    s = Scope(name="test", config_key="tests")
    assert s.name == "test"


def test_singularize_resources():
    from foundry.scope import _singularize

    assert _singularize("resources") == "resource"
    assert _singularize("apps") == "app"
    assert _singularize("s") == "s"
    assert _singularize("data") == "data"


def test_descends_through_non_list_basemodel_fields():
    """Non-list BaseModel fields are traversed, their inner lists surfaced."""

    class Widget(BaseModel):
        name: str

    class Middle(BaseModel):
        widgets: list[Widget] = []

    class Root(BaseModel):
        middle: Middle = Middle()

    scopes = discover_scopes(Root)
    names = [s.name for s in scopes]
    assert "widget" in names
    widget = next(s for s in scopes if s.name == "widget")
    # The widget list lives at ``parent_instance.middle.widgets``.
    assert widget.resolve_path == ("middle", "widgets")
    assert widget.parent is PROJECT


def test_cycle_detection_terminates():
    """Recursive Pydantic models don't send discovery into a loop.

    A self-referential ``list[Node]`` field produces one scope
    at the top level and one nested scope under it; recursion
    stops there — no infinite chain.
    """

    class Node(BaseModel):
        name: str = ""
        kids: list["Node"] = []

    Node.model_rebuild()
    scopes = discover_scopes(Node)
    names = [s.name for s in scopes]
    # First descent into Node via `kids` is fine; the nested
    # `kids` field on that inner Node triggers the cycle break.
    assert names.count("kid") == 2
