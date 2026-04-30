"""Tests for ``ingot.filter_values``."""

from ingot.filter_values import FilterValuesRequest, resolved_limit


def test_filter_values_request_defaults():
    request = FilterValuesRequest()
    assert request.q is None
    assert request.limit is None


def test_filter_values_request_round_trip():
    request = FilterValuesRequest.model_validate({"q": "ac", "limit": 25})
    assert request.q == "ac"
    assert request.limit == 25


def test_resolved_limit_default_when_unset():
    assert resolved_limit(None) == 50


def test_resolved_limit_clamps_above_max():
    assert resolved_limit(10_000) == 200


def test_resolved_limit_clamps_below_one():
    assert resolved_limit(0) == 1
    assert resolved_limit(-5) == 1
