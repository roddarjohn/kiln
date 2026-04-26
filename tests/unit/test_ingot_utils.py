"""Tests for ingot.utils."""

from __future__ import annotations

from ingot.utils import run_once


def test_run_once_runs_first_call():
    calls: list[tuple] = []

    @run_once
    def fn(*args, **kwargs):
        calls.append((args, kwargs))

    fn(1, 2, k="v")
    assert calls == [((1, 2), {"k": "v"})]


def test_run_once_ignores_second_call():
    calls: list[int] = []

    @run_once
    def fn(x):
        calls.append(x)

    fn(1)
    fn(2)
    fn(3)
    assert calls == [1]


def test_run_once_argument_blind():
    # Distinguishes ``run_once`` from ``functools.cache``: a second
    # call with *different* args is still a no-op rather than a
    # fresh execution keyed on the new args.
    calls: list[int] = []

    @run_once
    def fn(x):
        calls.append(x)

    fn(1)
    fn(99)
    assert calls == [1]


def test_run_once_returns_none_even_when_wrapped_returns_value():
    @run_once
    def fn():
        return 42

    assert fn() is None


def test_run_once_preserves_metadata():
    @run_once
    def my_fn(x: int) -> None:
        """My docstring."""

    assert my_fn.__name__ == "my_fn"
    assert my_fn.__doc__ == "My docstring."


def test_run_once_isolates_state_per_decoration():
    # Two distinct decorated functions must not share the gate;
    # otherwise calling one would silently disable the other.
    a_calls: list[int] = []
    b_calls: list[int] = []

    @run_once
    def a():
        a_calls.append(1)

    @run_once
    def b():
        b_calls.append(1)

    a()
    a()
    b()
    b()
    assert a_calls == [1]
    assert b_calls == [1]
