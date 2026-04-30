"""Naming helpers shared by ops and the renderer.

These are pure functions on a model dotted-path / :class:`~foundry.naming.Name`.
Living in a leaf module keeps the rest of the operations package
free of the cycle that would otherwise arise from
:mod:`be.operations.renderers` importing :mod:`be.operations.list`
(for ``ListResult``) while ``list`` reaches for naming helpers.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from foundry.naming import Name


def object_specs_const(model: Name) -> str:
    """Return the per-resource object-actions registry constant name."""
    return f"{model.raw.upper()}_OBJECT_ACTIONS"


def collection_specs_const(model: Name) -> str:
    """Return the per-resource collection-actions registry constant name."""
    return f"{model.raw.upper()}_COLLECTION_ACTIONS"
