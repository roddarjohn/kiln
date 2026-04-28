"""Pydantic schema for kiln_root configs.

Intentionally minimal: kiln_root is a one-shot bootstrap, so the
config only carries the few project-identity fields the templates
need to interpolate.  Everything else (dependency versions, the
shape of the generated ``project.jsonnet`` skeleton, the FastAPI
app factory) is fixed in the templates -- users edit the rendered
files directly afterwards rather than passing more knobs through
the config.
"""

from pydantic import Field

from foundry.config import FoundryConfig


class RootConfig(FoundryConfig):
    """Top-level config for the ``kiln_root`` target.

    Attributes:
        name: Project name.  Used as the ``[project].name`` value
            in the generated ``pyproject.toml`` and as the
            FastAPI app's ``title``.  Should be a valid Python
            distribution name (lowercase, hyphens or
            underscores).
        module: Default Python package name for the user's first
            kiln app.  The generated starter ``config/{module}.jsonnet``
            file references it, and the bootstrap creates an
            empty ``{module}/`` package so kiln has somewhere to
            attach generated routes on the first ``just generate``.
        description: Free-form one-line description.  Flows into
            ``pyproject.toml`` and the FastAPI app's
            ``description`` for parity with the generated OpenAPI
            spec.

    """

    name: str = Field(default="myapp")
    module: str = Field(default="app")
    description: str = Field(
        default="FastAPI app bootstrapped by kiln_root.",
    )
