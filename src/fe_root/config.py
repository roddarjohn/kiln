"""Pydantic schema for fe_root configs.

Bootstrap a yarn/TypeScript/React/Vite project with the
`@roddarjohn/glaze` component library and the React-Query
runtime that the ``fe`` target's generated hooks depend on.
"""

from pydantic import Field

from foundry.config import FoundryConfig


class RootConfig(FoundryConfig):
    """Top-level config for the ``fe_root`` target.

    Attributes:
        name: Project name.  Used as the ``"name"`` field in the
            generated ``package.json`` and as the default
            ``<title>`` in ``index.html``.  Should be a valid npm
            package name (lowercase, hyphens).
        description: Free-form one-line description.  Flows into
            ``package.json`` and the index.html ``<meta>``.
        glaze: When ``True`` (default), wire the
            `@roddarjohn/glaze <https://github.com/roddarjohn/glaze>`_
            component library into ``package.json`` and import
            its global stylesheet from ``src/index.css``.  Off
            disables both -- the scaffold is still a valid
            React+TS+Vite project, just without the design
            system.
        editable: When ``True``, pin local sibling-repo paths
            for development against unreleased changes:

            * ``"@roddarjohn/glaze": "file:../glaze"`` in
              ``package.json``.

            Drop before publishing -- the file-based version
            specifier won't resolve outside the author's
            machine.
        openapi_spec: Path (relative to the bootstrap output
            dir) where the ``fe`` target should look for the
            OpenAPI spec.  Defaults to ``"../be/openapi.json"``,
            which matches the monorepo layout
            (``be/`` next to ``fe/``); set explicitly when the
            spec lives elsewhere.

    """

    name: str = Field(default="myapp")
    description: str = Field(
        default="Frontend application bootstrapped by fe_root.",
    )
    glaze: bool = Field(default=True)
    editable: bool = Field(default=False)
    openapi_spec: str = Field(default="../be/openapi.json")
