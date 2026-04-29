"""Tests for the inheritance cascade primitives."""

from be.config.schema import OperationConfig
from be.operations._overrides import resolve_op_overrides
from foundry.cascade import cascade


def _ops(**name_to_value: object) -> list[OperationConfig]:
    """Build OperationConfig list with a ``rate_limit`` attr on each."""
    return [
        OperationConfig(name=name, rate_limit=value)
        for name, value in name_to_value.items()
    ]


# ---------------------------------------------------------------------------
# cascade
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# resolve_op_overrides
# ---------------------------------------------------------------------------


class TestResolveOpOverridesNoDisable:
    def test_op_value_wins_over_fallback(self):
        ops = _ops(get="5/minute", list=None)
        out = resolve_op_overrides(
            ops, attr="rate_limit", fallbacks=("100/minute",)
        )
        assert out == {"get": "5/minute", "list": "100/minute"}

    def test_multi_level_fallbacks_resolve_left_to_right(self):
        # First fallback wins when it's set.
        ops = _ops(get=None)
        out = resolve_op_overrides(
            ops,
            attr="rate_limit",
            fallbacks=("50/minute", "100/minute"),
        )
        assert out == {"get": "50/minute"}

    def test_falls_through_to_deeper_fallback(self):
        ops = _ops(get=None)
        out = resolve_op_overrides(
            ops,
            attr="rate_limit",
            fallbacks=(None, "100/minute"),
        )
        assert out == {"get": "100/minute"}

    def test_all_levels_none_yields_none(self):
        ops = _ops(get=None)
        out = resolve_op_overrides(
            ops, attr="rate_limit", fallbacks=(None, None)
        )
        assert out == {"get": None}

    def test_empty_operations_yields_empty_dict(self):
        out = resolve_op_overrides(
            [], attr="rate_limit", fallbacks=("100/minute",)
        )
        assert out == {}


class TestResolveOpOverridesWithDisable:
    def test_disable_sentinel_maps_to_none(self):
        ops = _ops(get=False, list="5/minute", create=None)
        out = resolve_op_overrides(
            ops,
            attr="rate_limit",
            fallbacks=("100/minute",),
            disable=False,
        )
        assert out == {
            "get": None,  # disabled at op level
            "list": "5/minute",  # explicit override
            "create": "100/minute",  # inherited
        }

    def test_disable_at_fallback_level_also_short_circuits(self):
        ops = _ops(get=None)
        out = resolve_op_overrides(
            ops,
            attr="rate_limit",
            fallbacks=(False, "100/minute"),
            disable=False,
        )
        # The fallback's ``False`` short-circuits before
        # ``"100/minute"`` is reached.
        assert out == {"get": None}
