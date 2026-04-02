"""Generator that produces pgcraft declarative model files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators._helpers import PGCRAFT_FACTORIES, column_def
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import FieldConfig, KilnConfig, ModelConfig


class PGCraftModelGenerator:
    """Produces one pgcraft declarative model file per model in config.

    Each generated file defines a class that inherits from the
    project's ``Base`` (a ``PGCraftBase`` subclass) and carries a
    ``__pgcraft__`` attribute that drives the factory pipeline.

    Generated files are always overwritten on re-generation so that
    column and plugin changes are picked up automatically.
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "pgcraft_models"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when the config contains at least one model.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.models)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate one model file per model in *config*.

        Args:
            config: The validated kiln configuration.

        Returns:
            One :class:`~kiln.generators.base.GeneratedFile` per
            model, written to ``db/models/<name_lower>.py``.

        """
        return [
            GeneratedFile(
                path=f"db/models/{m.name.lower()}.py",
                content=_render_model(m, config.module),
            )
            for m in config.models
        ]


def _render_model(model: ModelConfig, module: str) -> str:
    """Render the full Python source for a pgcraft model class.

    Args:
        model: The model configuration to render.
        module: Root module name for generated import paths.

    Returns:
        Python source string.

    """
    fields = model.fields
    factory_class, factory_module = PGCRAFT_FACTORIES[model.pgcraft_type]
    has_postgrest = "postgrest" in model.pgcraft_plugins
    has_fk = any(f.foreign_key for f in fields)

    pgcraft_items = [factory_class]
    if has_postgrest:
        pgcraft_items.append("PostgRESTPlugin()")

    dt_parts: list[str] = []
    if any(f.type == "datetime" for f in fields):
        dt_parts.append("datetime")
    if any(f.type == "date" for f in fields):
        dt_parts.append("date")

    sa_names = ["Column"]
    if any(f.type in ("str", "email") for f in fields):
        sa_names.append("String")
    if any(f.type == "int" for f in fields):
        sa_names.append("Integer")
    if any(f.type == "float" for f in fields):
        sa_names.append("Float")
    if any(f.type == "bool" for f in fields):
        sa_names.append("Boolean")
    if any(f.auto_now_add or f.auto_now for f in fields):
        sa_names.append("func")

    tmpl = env.get_template("fastapi/model.py.j2")
    return tmpl.render(
        model=model,
        module=module,
        needs_uuid=any(f.type == "uuid" for f in fields),
        dt_imports=", ".join(dt_parts),
        needs_pg=any(
            f.type in ("uuid", "datetime", "date", "json") for f in fields
        ),
        sa_imports=", ".join(sa_names),
        has_fk=has_fk,
        has_postgrest=has_postgrest,
        factory_class=factory_class,
        factory_module=factory_module,
        pgcraft_list=", ".join(pgcraft_items),
        columns=[
            {"name": f.name, "coldef": column_def(f)} for f in fields
        ],
    )


def _needs_uuid(fields: list[FieldConfig]) -> bool:
    return any(f.type == "uuid" for f in fields)
