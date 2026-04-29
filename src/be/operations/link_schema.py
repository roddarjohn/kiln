"""Resource-scope op that emits the per-resource link schema.

Each opted-in resource gets a typed Pydantic schema named
``{Model}Link`` (e.g. ``CustomerLink``, ``ProductLink``) emitted
into ``_generated/{app}/schemas/{slug}.py``.  The schema's
``type`` field is a ``Literal[<slug>]`` so the FE-side OpenAPI
client gets a proper discriminated type instead of every link
collapsing to the same ``{type, id, name}`` shape.

Gated by :attr:`~be.config.schema.ResourceConfig.link`; resources
without a link config emit nothing here.
"""

from typing import TYPE_CHECKING

from be.config.schema import PYTHON_TYPES
from be.operations.types import SchemaClass
from foundry.naming import Name
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("link_schema", scope="resource")
class LinkSchema:
    """Emit ``{Model}Link`` for resources with a link config."""

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run only when the resource declares a link config."""
        return ctx.instance.link is not None

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Yield one :class:`SchemaClass` for the resource's link.

        The shape varies with :attr:`LinkConfig.kind`: ``name``
        carries a single ``name`` field, ``id`` carries an ``id``
        typed against ``pk_type``, and ``id_name`` carries both.
        """
        resource = ctx.instance
        link = resource.link

        if link is None:  # pragma: no cover -- when() filters this
            msg = "link_schema op fired without link config"
            raise AssertionError(msg)

        _, model = Name.from_dotted(resource.model)
        slug = model.lower
        id_py_type = PYTHON_TYPES[resource.pk_type]
        extra_imports: list[tuple[str, str]] = [("typing", "Literal")]
        # uuid import is plain ``import uuid`` not from-import, so
        # the renderer's plain-import collector handles it via the
        # py_type string containing "uuid.UUID".  pk_type uuid maps
        # to "uuid.UUID" in PYTHON_TYPES; the import collector picks
        # the leading "uuid" out of any annotation that contains it.

        yield SchemaClass(
            name=f"{model.pascal}Link",
            body_template="fastapi/schema_parts/link.py.j2",
            body_context={
                "schema_name": f"{model.pascal}Link",
                "slug": slug,
                "kind": link.kind,
                "id_py_type": id_py_type,
            },
            extra_imports=extra_imports,
        )
