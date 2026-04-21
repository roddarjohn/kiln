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


def test_scope_parent_is_project():
    scopes = discover_scopes(TopConfig)
    for s in scopes[1:]:
        assert s.parent is PROJECT


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
