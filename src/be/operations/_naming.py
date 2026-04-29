"""Naming helpers shared by ops and the renderer.

These are pure functions on a model dotted-path / :class:`~foundry.naming.Name`.
Living in a leaf module keeps the rest of the operations package
free of the cycle that would otherwise arise from
:mod:`be.operations.renderers` importing :mod:`be.operations.list`
(for ``ListResult``) while ``list`` reaches for naming helpers.
"""

from foundry.naming import Name


def app_module_for(model_path: str) -> str:
    """Return the consumer's app package from a model dotted path.

    Strips the model class name, then the trailing ``.models`` (or
    similar) segment.  Examples::

        "blog.models.Article"  -> "blog"
        "myapp.users.User"     -> "myapp"
        "single.Model"         -> "single"

    Generated code lives at ``{package_prefix}.{app}.{...}``, so
    every site that needs to import from a sibling generated
    module (serializers, schemas, action registry) routes through
    this helper.
    """
    return Name.parent_path(Name.parent_path(model_path))


def object_specs_const(model: Name) -> str:
    """Return the per-resource object-actions registry constant name."""
    return f"{model.raw.upper()}_OBJECT_ACTIONS"


def collection_specs_const(model: Name) -> str:
    """Return the per-resource collection-actions registry constant name."""
    return f"{model.raw.upper()}_COLLECTION_ACTIONS"
