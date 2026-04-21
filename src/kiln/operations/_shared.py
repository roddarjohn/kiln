"""Shared helpers used by the per-op operation modules.

Each CRUD op (get, list, create, update, delete) pulls the same
field-conversion helpers from here to avoid repetition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from foundry.outputs import Field, SchemaClass, SerializerFn
from kiln.config.schema import FieldSpec  # noqa: TC001
from kiln.generators._helpers import PYTHON_TYPES

if TYPE_CHECKING:
    from foundry.naming import Name


class FieldsOptions(BaseModel):
    """Options for operations that require a field list."""

    fields: list[FieldSpec]


def _field_dicts(fields: list[FieldSpec]) -> list[Field]:
    """Convert config FieldSpecs to Fields."""
    return [
        Field(
            name=f.name,
            py_type=PYTHON_TYPES[f.type],
        )
        for f in fields
    ]


def _read_schema_outputs(
    model: Name,
    fields: list[FieldSpec],
    suffix: str,
    serializer_stem: str,
) -> tuple[SchemaClass, SerializerFn]:
    """Build the ``SchemaClass`` / ``SerializerFn`` pair for a read op.

    ``suffix`` is appended to the model's pascal-cased name to form
    the response schema class (e.g. ``Resource`` -> ``UserResource``).
    ``serializer_stem`` becomes the trailing segment of the
    serializer function, e.g. ``resource`` -> ``to_user_resource``.
    """
    out_fields = _field_dicts(fields)
    schema_name = model.suffixed(suffix)
    serializer_fn = f"to_{model.lower}_{serializer_stem}"
    schema = SchemaClass(
        name=schema_name,
        fields=out_fields,
        doc=f"{suffix} schema for {model.pascal}.",
    )
    serializer = SerializerFn(
        function_name=serializer_fn,
        model_name=model.pascal,
        schema_name=schema_name,
        fields=out_fields,
    )
    return schema, serializer
