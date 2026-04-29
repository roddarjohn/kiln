"""Tests for the inheritance cascade primitive."""

from foundry.cascade import cascade


class TestCascade:
    def test_first_non_none_wins(self):
        assert cascade(None, "5/min", "100/min") == "5/min"

    def test_op_value_overrides_lower_levels(self):
        assert cascade("2/sec", "5/min", "100/min") == "2/sec"

    def test_falls_through_to_lowest_level(self):
        assert cascade(None, None, "100/min") == "100/min"

    def test_all_none_returns_none(self):
        assert cascade(None, None, None) is None

    def test_no_levels_returns_none(self):
        assert cascade() is None

    def test_disable_at_op_level_short_circuits(self):
        # ``False`` at the op level kills the chain even though
        # later fallbacks are set.
        op_value = False
        assert cascade(op_value, "5/min", "100/min", disable=False) is None

    def test_disable_at_middle_level_short_circuits(self):
        # The walk stops as soon as it encounters disable -- a
        # later non-None fallback is *not* honoured.
        resource_value = False
        assert cascade(None, resource_value, "100/min", disable=False) is None

    def test_disable_only_active_when_provided(self):
        # Without ``disable``, ``False`` is just a value -- the
        # first non-None.
        resource_value = False
        assert cascade(None, resource_value, "100/min") is False

    def test_disable_can_be_arbitrary_value(self):
        assert cascade(None, "off", "100/min", disable="off") is None
