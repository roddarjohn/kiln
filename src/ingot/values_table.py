"""Build a SQL ``VALUES`` selectable from a dataclass + its instances.

Useful when a fixed set of records — an enum, a constants table, a
preference matrix — needs to participate in the same query
machinery as real tables: keyset pagination, ORDER BY, ILIKE
filtering, ``select(...).where(...)`` composition.

The utility introspects the dataclass to derive column names and
SQL types, then assembles a SQLAlchemy :class:`~sqlalchemy.Values`
selectable populated from each instance.  Callers index columns
via the standard ``.c.<name>`` accessor::

    @dataclass
    class Choice:
        value: str
        label: str

    table = values_table(Choice, [Choice("a", "Alpha"), Choice("b", "Beta")])
    stmt = (
        select(table.c.value, table.c.label)
        .where(table.c.label.ilike("%alpha%"))
        .order_by(table.c.label)
    )

Type mapping covers the common scalar types only — extending it
means adding to :data:`_PYTHON_TO_SA`.  ``Any``-typed fields are
emitted as ``String`` (the wire-friendly default).
"""

from __future__ import annotations

import datetime as _datetime
import uuid
from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING, Any, get_type_hints

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Uuid,
    column,
    values,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.selectable import Values
    from sqlalchemy.types import TypeEngine


_PYTHON_TO_SA: dict[type, type[TypeEngine[Any]]] = {
    str: String,
    int: Integer,
    bool: Boolean,
    float: Float,
    uuid.UUID: Uuid,
    _datetime.date: Date,
    _datetime.datetime: DateTime,
}
"""Map of Python primitive types → SQLAlchemy column type classes.
Extended on a need-to basis; uncommon types should be added here
rather than worked around at the call site."""


def values_table(
    dataclass_type: type,
    instances: Sequence[Any],
    *,
    name: str = "t",
) -> Values:
    """Build a SQL ``VALUES`` selectable from *instances*.

    Args:
        dataclass_type: A ``@dataclass`` whose fields define the
            synthetic table's columns.  Each field's annotation
            picks the SQLAlchemy column type via
            :data:`_PYTHON_TO_SA`.
        instances: Sequence of instances.  Each becomes one row
            in the order given.
        name: Alias the VALUES clause is given in SQL — needs to
            be unique within a query.

    Returns:
        A SQLAlchemy values selectable.  Index columns via
        ``result.c.<field_name>``; compose ``select(...)``,
        ``where(...)``, ``order_by(...)`` against it the same way
        you would a real table.

    Raises:
        TypeError: If *dataclass_type* isn't a dataclass, or one
            of its fields is annotated with a type not in
            :data:`_PYTHON_TO_SA`.

    """
    if not is_dataclass(dataclass_type):
        msg = f"{dataclass_type!r} is not a @dataclass"
        raise TypeError(msg)

    type_hints = get_type_hints(dataclass_type)
    columns_for_alias = []
    field_names: list[str] = []

    for dataclass_field in fields(dataclass_type):
        py_type = type_hints[dataclass_field.name]
        sa_type_cls = _resolve_sa_type(py_type)
        columns_for_alias.append(column(dataclass_field.name, sa_type_cls()))
        field_names.append(dataclass_field.name)

    rows = [
        tuple(getattr(instance, name) for name in field_names)
        for instance in instances
    ]

    return values(*columns_for_alias, name=name).data(rows)


def _resolve_sa_type(py_type: Any) -> type[TypeEngine[Any]]:
    """Map a Python type annotation to a SQLAlchemy type class.

    Falls back to :class:`~sqlalchemy.String` for ``Any`` so
    free-form ``value: Any`` columns still produce a SQL-emittable
    ``VALUES`` clause.  Unsupported concrete types raise
    :class:`TypeError` so the failure is loud at codegen time
    rather than silently coercing data.
    """
    if py_type is Any:
        return String

    if py_type in _PYTHON_TO_SA:
        return _PYTHON_TO_SA[py_type]

    msg = (
        f"values_table: unsupported field type {py_type!r}.  "
        f"Add it to ingot.values_table._PYTHON_TO_SA if it should "
        f"map to a SQLAlchemy type."
    )
    raise TypeError(msg)
