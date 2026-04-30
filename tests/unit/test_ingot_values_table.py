"""Tests for :mod:`ingot.values_table`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from ingot.values_table import values_table


def _sql(stmt: Any) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).replace("\n", " ")


@dataclass(frozen=True)
class _Choice:
    value: str
    label: str


@dataclass(frozen=True)
class _AnyTyped:
    value: Any


def test_values_table_emits_values_clause_with_each_row() -> None:
    table = values_table(
        _Choice,
        [_Choice(value="a", label="Alpha"), _Choice(value="b", label="Beta")],
        name="choices",
    )
    sql = _sql(select(table.c.value, table.c.label))
    assert "VALUES" in sql
    assert "'a'" in sql
    assert "'Alpha'" in sql
    assert "'Beta'" in sql


def test_values_table_rejects_non_dataclass() -> None:
    with pytest.raises(TypeError, match="not a @dataclass"):
        values_table(int, [1, 2])  # type: ignore[arg-type]


def test_values_table_accepts_any_typed_field_as_string() -> None:
    """``value: Any`` columns fall back to SQLAlchemy ``String``."""
    table = values_table(_AnyTyped, [_AnyTyped(value="hello")], name="anys")
    sql = _sql(select(table.c.value))
    assert "'hello'" in sql


def test_values_table_rejects_unsupported_field_type() -> None:
    @dataclass(frozen=True)
    class _Bad:
        value: bytes  # not in _PYTHON_TO_SA

    with pytest.raises(TypeError, match="unsupported field type"):
        values_table(_Bad, [_Bad(value=b"x")])
