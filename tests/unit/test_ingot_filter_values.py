"""Tests for ``ingot.filter_values``."""

import enum

from ingot.filter_values import (
    FilterValuesRequest,
    enum_values,
    paginate_in_memory,
    resolved_limit,
    resolved_offset,
)


class _Color(enum.StrEnum):
    """Sample enum used by the tests below."""

    RED = enum.auto()
    GREEN = enum.auto()
    BLUE = enum.auto()
    REDDISH = enum.auto()


def test_filter_values_request_defaults():
    req = FilterValuesRequest()
    assert req.q is None
    assert req.cursor is None
    assert req.limit is None


def test_filter_values_request_round_trip():
    req = FilterValuesRequest.model_validate(
        {"q": "ac", "cursor": "50", "limit": 25}
    )
    assert req.q == "ac"
    assert req.cursor == "50"
    assert req.limit == 25


def test_resolved_limit_default_when_unset():
    assert resolved_limit(None) == 50


def test_resolved_limit_clamps_above_max():
    assert resolved_limit(10_000) == 200


def test_resolved_limit_clamps_below_one():
    assert resolved_limit(0) == 1
    assert resolved_limit(-5) == 1


def test_resolved_offset_default_when_unset():
    assert resolved_offset(None) == 0
    assert resolved_offset("") == 0


def test_resolved_offset_parses_int():
    assert resolved_offset("50") == 50


def test_resolved_offset_falls_back_when_invalid():
    assert resolved_offset("not-a-number") == 0


def test_resolved_offset_clamps_negative():
    assert resolved_offset("-5") == 0


def test_paginate_in_memory_first_page():
    items = [{"value": str(i)} for i in range(120)]
    page = paginate_in_memory(items, FilterValuesRequest(limit=20))
    assert len(page["results"]) == 20
    assert page["next_cursor"] == "20"


def test_paginate_in_memory_with_cursor():
    items = [{"value": str(i)} for i in range(60)]
    page = paginate_in_memory(items, FilterValuesRequest(cursor="40", limit=20))
    assert page["results"][0]["value"] == "40"
    assert page["next_cursor"] is None


def test_paginate_in_memory_last_page_signals_done():
    items = [{"value": str(i)} for i in range(10)]
    page = paginate_in_memory(items, FilterValuesRequest(limit=50))
    assert len(page["results"]) == 10
    assert page["next_cursor"] is None


def test_enum_values_emits_value_label_pairs():
    page = enum_values(_Color, FilterValuesRequest())
    by_value = {m["value"]: m["label"] for m in page["results"]}
    assert by_value == {
        "red": "RED",
        "green": "GREEN",
        "blue": "BLUE",
        "reddish": "REDDISH",
    }
    assert page["next_cursor"] is None


def test_enum_values_q_matches_value():
    page = enum_values(_Color, FilterValuesRequest(q="red"))
    values = {m["value"] for m in page["results"]}
    assert values == {"red", "reddish"}


def test_enum_values_q_is_case_insensitive():
    page = enum_values(_Color, FilterValuesRequest(q="GR"))
    values = {m["value"] for m in page["results"]}
    assert values == {"green"}


def test_enum_values_paginates_when_limit_below_total():
    page = enum_values(_Color, FilterValuesRequest(limit=2))
    assert len(page["results"]) == 2
    assert page["next_cursor"] == "2"
