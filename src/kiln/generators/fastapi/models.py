"""Generator that produces pgcraft declarative model files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators._helpers import (
    column_def,
    resolve_pk_plugin,
    split_dotted_class,
    type_imports,
)
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig, ModelConfig

from typing import Any


def _py_literal(value: Any) -> str:  # noqa: ANN401
    """Convert a JSON-compatible value to a Python literal string.

    Args:
        value: A JSON-compatible value (str, int, float, bool, None, list).

    Returns:
        Python source representation, e.g. ``'"api"'`` or ``'["select"]'``.

    """
    return repr(value)


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
            model, written to ``{module}/models/<name_lower>.py``.

        """
        app = config.module
        files: list[GeneratedFile] = [
            GeneratedFile(path=f"{app}/models/__init__.py", content=""),
        ]
        files.extend(
            GeneratedFile(
                path=f"{app}/models/{m.name.lower()}.py",
                content=_render_model(m, config.module),
            )
            for m in config.models
        )
        return files


def _render_model(model: ModelConfig, module: str) -> str:
    """Render the full Python source for a pgcraft model class.

    Args:
        model: The model configuration to render.
        module: Root module name for generated import paths.

    Returns:
        Python source string.

    """
    fields = model.fields
    pk_field = next((f for f in fields if f.primary_key), None)
    non_pk_fields = [f for f in fields if not f.primary_key]

    factory_module, factory_class = split_dotted_class(model.pgcraft_type)
    has_fk = any(f.foreign_key for f in non_pk_fields)

    # Resolve the PK plugin and build the __pgcraft__ list.
    pgcraft_items = [factory_class]
    pk_plugin_import: str | None = None
    if pk_field is not None:
        pk_plugin_path = resolve_pk_plugin(pk_field.type, pk_field.primary_key)
        pk_plugin_module, pk_plugin_class = split_dotted_class(pk_plugin_path)
        pk_args = (
            f'column_name="{pk_field.name}"' if pk_field.name != "id" else ""
        )
        plugin_call = (
            f"{pk_plugin_class}({pk_args})"
            if pk_args
            else f"{pk_plugin_class}()"
        )
        pgcraft_items.append(plugin_call)
        pk_plugin_import = f"from {pk_plugin_module} import {pk_plugin_class}"

    # Additional plugins — strings or PluginRef objects.
    extra_plugin_imports: list[str] = []
    for plugin in model.pgcraft_plugins:
        path = plugin if isinstance(plugin, str) else plugin.path
        plugin_mod, plugin_cls = split_dotted_class(path)
        extra_plugin_imports.append(f"from {plugin_mod} import {plugin_cls}")
        if isinstance(plugin, str) or not plugin.args:
            pgcraft_items.append(f"{plugin_cls}()")
        else:
            kwargs = ", ".join(
                f"{k}={_py_literal(v)}" for k, v in plugin.args.items()
            )
            pgcraft_items.append(f"{plugin_cls}({kwargs})")

    sa_names = ["Column"]
    if any(f.type in ("str", "email") for f in non_pk_fields):
        sa_names.append("String")
    if any(f.type == "int" for f in non_pk_fields):
        sa_names.append("Integer")
    if any(f.type == "float" for f in non_pk_fields):
        sa_names.append("Float")
    if any(f.type == "bool" for f in non_pk_fields):
        sa_names.append("Boolean")
    if any(f.auto_now_add or f.auto_now for f in non_pk_fields):
        sa_names.append("func")

    tmpl = env.get_template("fastapi/model.py.j2")
    return tmpl.render(
        model=model,
        module=module,
        imports=type_imports([f.type for f in non_pk_fields]),
        needs_pg=any(
            f.type in ("uuid", "datetime", "date", "json")
            for f in non_pk_fields
        ),
        sa_imports=", ".join(sa_names),
        has_fk=has_fk,
        pk_plugin_import=pk_plugin_import,
        extra_plugin_imports=extra_plugin_imports,
        factory_class=factory_class,
        factory_module=factory_module,
        pgcraft_list=", ".join(pgcraft_items),
        columns=[
            {"name": f.name, "coldef": column_def(f)} for f in non_pk_fields
        ],
    )
