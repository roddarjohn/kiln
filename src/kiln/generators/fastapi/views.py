"""Generator for FastAPI view and function route files.

For each :class:`~kiln.config.schema.ViewModel` in the config one
route file is produced (``<name>/route.py``):

- Non-parameterised views call a developer-supplied ``query_fn``
  (a zero-argument function returning a SQLAlchemy ``select()``)
  specified in the config.
- Parameterised views call the named set-returning function via
  ``func.<schema>.<name>(params).table_valued(cols)``.

No stub files are generated — the developer owns the query/function
definitions and points kiln at them via ``query_fn``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators._helpers import (
    PYTHON_TYPES,
    SA_INSTANCE_TYPES,
    resolve_db_session,
    type_imports,
)
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import DatabaseConfig, KilnConfig, ViewModel


class ViewGenerator:
    """Generates FastAPI routes for views and set-returning functions."""

    skip_validation: bool = False

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
        """Produce one route file per view in *config*.

        Args:
            config: The validated kiln configuration.

        Returns:
            One :class:`~kiln.generators.base.GeneratedFile` per view,
            written to ``<name>/route.py``.

        """
        if not ViewGenerator.skip_validation:
            _validate_query_fns(config.views)
        return [
            GeneratedFile(
                path=f"routes/{view.name}.py",
                content=_render_route(view, config.module, config.databases),
            )
            for view in config.views
        ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_query_fns(views: list[ViewModel]) -> None:
    """Raise ValueError for any plain view whose query_fn module file is absent.

    Checks that the module path resolves to a ``.py`` file relative to the
    current working directory.  Pass ``--no-validate`` to skip this check
    when the module hasn't been written yet.

    Args:
        views: All view configs to validate.

    """
    cwd = Path.cwd()
    errors: list[str] = []
    for view in views:
        if view.parameters or not view.query_fn:
            continue
        module_path, _ = view.query_fn.rsplit(".", 1)
        file_path = cwd / Path(*module_path.split("."))
        if not file_path.with_suffix(".py").exists():
            errors.append(
                f"  view '{view.name}': module '{module_path}' not found"
                f" (from query_fn='{view.query_fn}')"
            )
    if errors:
        msg = "query_fn modules could not be found:\n" + "\n".join(errors)
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Route rendering
# ---------------------------------------------------------------------------


def _render_route(
    view: ViewModel,
    module: str,
    databases: list[DatabaseConfig],
) -> str:
    """Render the FastAPI route file for *view*.

    Args:
        view: The view configuration.
        module: Root module name for generated import paths.
        databases: The project-level database list.

    Returns:
        Python source string.

    """
    session_module, get_db_fn = resolve_db_session(view.db_key, databases)
    if view.parameters:
        return _render_function_route(view, module, session_module, get_db_fn)
    return _render_view_route(view, module, session_module, get_db_fn)


def _render_view_route(
    view: ViewModel,
    module: str,
    session_module: str,
    get_db_fn: str,
) -> str:
    """Route that calls the developer-supplied query_fn."""
    if not view.query_fn:
        msg = (
            f"View '{view.name}' has no parameters but is missing "
            f"'query_fn'. Provide a dotted import path to a function "
            f"that returns a SQLAlchemy select(), e.g. "
            f'"app.db.views.{view.name}.get_query".'
        )
        raise ValueError(msg)

    query_fn_module, query_fn_name = view.query_fn.rsplit(".", 1)

    tmpl = env.get_template("fastapi/view_route_plain.py.j2")
    return tmpl.render(
        view=view,
        module=module,
        result_class=_pascal(view.name) + "Result",
        has_auth=view.require_auth,
        method=view.http_method.lower(),
        slug=view.name.replace("_", "-"),
        imports=type_imports([c.type for c in view.returns]),
        description=view.description or f"Query the {view.name} view.",
        query_fn_module=query_fn_module,
        query_fn_name=query_fn_name,
        session_module=session_module,
        get_db_fn=get_db_fn,
        columns=[
            {"name": c.name, "py_type": PYTHON_TYPES[c.type]}
            for c in view.returns
        ],
    )


def _render_function_route(
    view: ViewModel,
    module: str,
    session_module: str,
    get_db_fn: str,
) -> str:
    """Route that calls a set-returning function via func.table_valued."""
    all_items = list(view.parameters) + list(view.returns)
    tv_cols = ", ".join(
        f'column("{c.name}", {SA_INSTANCE_TYPES[c.type]})' for c in view.returns
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
        imports=type_imports([c.type for c in all_items]),
        description=view.description or f"Call the {view.name} function.",
        session_module=session_module,
        get_db_fn=get_db_fn,
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
