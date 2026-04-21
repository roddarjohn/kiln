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


def _construct_response_schema(
    model: Name,
    fields: list[FieldSpec],
    suffix: str,
) -> SchemaClass:
    """Build the response ``SchemaClass`` for a read op.

    ``suffix`` is appended to the model's pascal-cased name to form
    the schema class (e.g. ``Resource`` -> ``UserResource``).
    """
    return SchemaClass(
        name=model.suffixed(suffix),
        fields=_field_dicts(fields),
        doc=f"{suffix} schema for {model.pascal}.",
    )


def _construct_serializer(
    model: Name,
    schema: SchemaClass,
    stem: str,
) -> SerializerFn:
    """Build the ``SerializerFn`` that maps a model row to ``schema``.

    ``stem`` becomes the trailing segment of the serializer
    function, e.g. ``resource`` -> ``to_user_resource``.
    """
    return SerializerFn(
        function_name=f"to_{model.lower}_{stem}",
        model_name=model.pascal,
        schema_name=schema.name,
        fields=schema.fields,
    )
