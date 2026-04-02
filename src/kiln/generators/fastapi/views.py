"""Generator for pgcraft view/function stubs and FastAPI view routes.

For each :class:`~kiln.config.schema.ViewModel` in the config two
files are produced:

1. **pgcraft stub** (``db/views/<name>.py``, ``overwrite=False``) —
   a scaffold the developer fills in with the actual SQL query or
   function body.  Non-parameterised views get a
   ``PGCraftViewMixin`` stub; parameterised ones get a
   ``PGCraftFunctionMixin`` stub.

2. **FastAPI route** (``api/views/<name>.py``, ``overwrite=True``) —
   queries the named database object without any inline SQL:

   - Views: ``select(view_table)``
   - Functions: ``func.<schema>.<name>(params).table_valued(cols)``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators._helpers import (
    PG_SQL_TYPES,
    PYTHON_TYPES,
    SA_INSTANCE_TYPES,
)
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig, ViewModel


class ViewGenerator:
    """Generates pgcraft stubs and FastAPI routes for views/functions."""

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "views"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when the config has at least one view.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.views)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Produce stub and route files for each view in *config*.

        Args:
            config: The validated kiln configuration.

        Returns:
            Two :class:`~kiln.generators.base.GeneratedFile` objects
            per view: a pgcraft stub and a FastAPI route file.

        """
        files: list[GeneratedFile] = []
        for view in config.views:
            files.append(
                GeneratedFile(
                    path=f"{view.name}/stub.py",
                    content=_render_stub(view, config.module),
                    overwrite=False,
                )
            )
            files.append(
                GeneratedFile(
                    path=f"{view.name}/route.py",
                    content=_render_route(view, config.module),
                    overwrite=True,
                )
            )
        return files


# ---------------------------------------------------------------------------
# Stub rendering
# ---------------------------------------------------------------------------


def _render_stub(view: ViewModel, module: str) -> str:
    """Render the pgcraft stub file for *view*.

    Args:
        view: The view configuration.
        module: Root module name for generated import paths.

    Returns:
        Python source string (scaffold, not a complete definition).

    """
    if view.parameters:
        return _render_function_stub(view, module)
    return _render_view_stub(view, module)


def _render_view_stub(view: ViewModel, module: str) -> str:
    """Stub for a non-parameterised pgcraft view."""
    tmpl = env.get_template("fastapi/view_stub_plain.py.j2")
    return tmpl.render(
        view=view,
        module=module,
        pascal_name=_pascal(view.name),
        columns=[
            {"name": c.name, "sa_instance_type": SA_INSTANCE_TYPES[c.type]}
            for c in view.returns
        ],
    )


def _render_function_stub(view: ViewModel, module: str) -> str:
    """Stub for a parameterised set-returning function."""
    returns_sql = ", ".join(
        f"{c.name} {PG_SQL_TYPES[c.type]}" for c in view.returns
    )
    tmpl = env.get_template("fastapi/view_stub_fn.py.j2")
    return tmpl.render(
        view=view,
        module=module,
        returns_sql=returns_sql,
    )


# ---------------------------------------------------------------------------
# Route rendering
# ---------------------------------------------------------------------------


def _render_route(view: ViewModel, module: str) -> str:
    """Render the FastAPI route file for *view*.

    Args:
        view: The view configuration.
        module: Root module name for generated import paths.

    Returns:
        Python source string.

    """
    if view.parameters:
        return _render_function_route(view, module)
    return _render_view_route(view, module)


def _render_view_route(view: ViewModel, module: str) -> str:
    """Route that queries a non-parameterised view via its SA Table."""
    all_cols = list(view.returns)
    dt_parts: list[str] = []
    if any(c.type == "datetime" for c in all_cols):
        dt_parts.append("datetime")
    if any(c.type == "date" for c in all_cols):
        dt_parts.append("date")

    tmpl = env.get_template("fastapi/view_route_plain.py.j2")
    return tmpl.render(
        view=view,
        module=module,
        result_class=_pascal(view.name) + "Result",
        has_auth=view.require_auth,
        method=view.http_method.lower(),
        slug=view.name.replace("_", "-"),
        needs_uuid=any(c.type == "uuid" for c in all_cols),
        dt_imports=", ".join(dt_parts),
        needs_any=any(c.type == "json" for c in all_cols),
        description=view.description or f"Query the {view.name} view.",
        columns=[
            {"name": c.name, "py_type": PYTHON_TYPES[c.type]}
            for c in view.returns
        ],
    )


def _render_function_route(view: ViewModel, module: str) -> str:
    """Route that calls a set-returning function via func.table_valued."""
    all_items = list(view.parameters) + list(view.returns)
    dt_parts: list[str] = []
    if any(c.type == "datetime" for c in all_items):
        dt_parts.append("datetime")
    if any(c.type == "date" for c in all_items):
        dt_parts.append("date")

    tv_cols = ", ".join(
        f'column("{c.name}", {SA_INSTANCE_TYPES[c.type]})'
        for c in view.returns
    )
    col_names_str = ", ".join(f'"{c.name}"' for c in view.returns)
    fn_args = ", ".join(p.name for p in view.parameters)

    tmpl = env.get_template("fastapi/view_route_fn.py.j2")
    return tmpl.render(
        view=view,
        module=module,
        result_class=_pascal(view.name) + "Result",
        has_auth=view.require_auth,
        method=view.http_method.lower(),
        slug=view.name.replace("_", "-"),
        needs_uuid=any(c.type == "uuid" for c in all_items),
        dt_imports=", ".join(dt_parts),
        needs_any=any(c.type == "json" for c in view.returns),
        description=view.description or f"Call the {view.name} function.",
        columns=[
            {"name": c.name, "py_type": PYTHON_TYPES[c.type]}
            for c in view.returns
        ],
        params=[
            {"name": p.name, "py_type": PYTHON_TYPES[p.type]}
            for p in view.parameters
        ],
        tv_cols=tv_cols,
        fn_args=fn_args,
        col_names_str=col_names_str,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pascal(name: str) -> str:
    """Convert snake_case *name* to PascalCase."""
    return "".join(part.capitalize() for part in name.split("_"))
