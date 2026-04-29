"""Tests for the per-op override resolver."""

from be.config.schema import OperationConfig
from be.operations._overrides import resolve_op_overrides


def _ops(**name_to_value: object) -> list[OperationConfig]:
    """Build OperationConfig list with a ``rate_limit`` attr on each."""
    return [
        OperationConfig(name=name, rate_limit=value)
        for name, value in name_to_value.items()
    ]


class TestResolveOpOverridesNoDisable:
    def test_op_value_wins_over_inherited(self):
        ops = _ops(get="5/minute", list=None)
        out = resolve_op_overrides(
            ops, attr="rate_limit", inherited="100/minute"
        )
        assert out == {"get": "5/minute", "list": "100/minute"}

    def test_inherited_can_be_none(self):
        # ``None`` inherited just means every op without an
        # override resolves to ``None``.
        ops = _ops(get=None, list=None)
        out = resolve_op_overrides(ops, attr="rate_limit", inherited=None)
        assert out == {"get": None, "list": None}

    def test_empty_operations_yields_empty_dict(self):
        assert resolve_op_overrides([], attr="rate_limit", inherited="x") == {}


class TestResolveOpOverridesWithDisable:
    def test_disable_sentinel_maps_to_none(self):
        ops = _ops(get=False, list="5/minute", create=None)
        out = resolve_op_overrides(
            ops,
            attr="rate_limit",
            inherited="100/minute",
            disable=False,
        )
        assert out == {
            "get": None,  # disabled
            "list": "5/minute",  # explicit override
            "create": "100/minute",  # inherited
        }

    def test_custom_disable_value(self):
        # Any object can act as the disable sentinel; here a
        # made-up string acts as the kill switch.
        ops = _ops(get="off", list="10/minute")
        out = resolve_op_overrides(
            ops,
            attr="rate_limit",
            inherited="100/minute",
            disable="off",
        )
        assert out == {"get": None, "list": "10/minute"}
