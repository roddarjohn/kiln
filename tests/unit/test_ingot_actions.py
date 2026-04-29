"""Tests for ingot.actions."""

from dataclasses import dataclass

import pytest

from ingot.actions import (
    ActionRef,
    ActionSpec,
    always_true,
    available_actions,
    filter_visible,
    find_can,
)


@dataclass
class Article:
    """Stand-in for a SQLAlchemy resource row."""

    id: int
    state: str = "draft"


@dataclass
class Session:
    """Stand-in for the consumer's session model."""

    role: str = "user"


async def _can_published(resource: Article, _session: Session) -> bool:
    return resource.state == "published"


async def _can_admin(_resource: object, session: Session) -> bool:
    return session.role == "admin"


async def test_always_true_returns_true():
    assert await always_true(None, None) is True
    assert await always_true(Article(id=1), Session()) is True


async def test_action_spec_scope_derives_from_is_object_action():
    obj = ActionSpec(name="update", can=always_true, is_object_action=True)
    coll = ActionSpec(name="create", can=always_true, is_object_action=False)
    assert obj.scope == "object"
    assert coll.scope == "collection"


async def test_available_actions_filters_by_guard():
    specs = [
        ActionSpec(name="get", can=always_true, is_object_action=True),
        ActionSpec(name="publish", can=_can_published, is_object_action=True),
    ]
    refs = await available_actions(
        Article(id=1, state="draft"), Session(), specs
    )
    assert refs == [ActionRef(name="get", scope="object")]

    refs = await available_actions(
        Article(id=2, state="published"), Session(), specs
    )
    assert refs == [
        ActionRef(name="get", scope="object"),
        ActionRef(name="publish", scope="object"),
    ]


async def test_available_actions_preserves_spec_order():
    specs = [
        ActionSpec(name="b", can=always_true, is_object_action=True),
        ActionSpec(name="a", can=always_true, is_object_action=True),
        ActionSpec(name="c", can=always_true, is_object_action=True),
    ]
    refs = await available_actions(Article(id=1), Session(), specs)
    assert [r.name for r in refs] == ["b", "a", "c"]


async def test_available_actions_collection_scope_passes_none_resource():
    captured: list[object] = []

    async def can(resource: object, _session: Session) -> bool:
        captured.append(resource)
        return True

    specs = [ActionSpec(name="create", can=can, is_object_action=False)]
    refs = await available_actions(None, Session(), specs)
    assert refs == [ActionRef(name="create", scope="collection")]
    assert captured == [None]


async def test_available_actions_uses_session_in_guard():
    specs = [ActionSpec(name="delete", can=_can_admin, is_object_action=True)]
    user_refs = await available_actions(Article(id=1), Session(), specs)
    admin_refs = await available_actions(
        Article(id=1), Session(role="admin"), specs
    )
    assert user_refs == []
    assert admin_refs == [ActionRef(name="delete", scope="object")]


async def test_available_actions_empty_specs_returns_empty_list():
    assert await available_actions(Article(id=1), Session(), []) == []


async def test_filter_visible_drops_rejected_rows():
    rows = [
        Article(id=1, state="draft"),
        Article(id=2, state="published"),
        Article(id=3, state="published"),
    ]
    survivors = await filter_visible(rows, Session(), _can_published)
    assert [r.id for r in survivors] == [2, 3]


async def test_filter_visible_preserves_input_order():
    rows = [Article(id=i, state="published") for i in [3, 1, 2]]
    survivors = await filter_visible(rows, Session(), _can_published)
    assert [r.id for r in survivors] == [3, 1, 2]


async def test_filter_visible_with_always_true_returns_all():
    rows = [Article(id=1), Article(id=2)]
    survivors = await filter_visible(rows, Session(), always_true)
    assert survivors == rows


def test_find_can_returns_matching_guard():
    specs = (
        ActionSpec(name="get", can=always_true, is_object_action=True),
        ActionSpec(name="list", can=_can_published, is_object_action=False),
    )
    assert find_can(specs, "list") is _can_published
    assert find_can(specs, "get") is always_true


def test_find_can_raises_for_unknown_name():
    specs = (ActionSpec(name="get", can=always_true, is_object_action=True),)

    with pytest.raises(KeyError, match="'publish'"):
        find_can(specs, "publish")
