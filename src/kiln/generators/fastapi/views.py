"""Generator for FastAPI view and action route files.

For each :class:`~kiln.config.schema.ViewRouteConfig` or
:class:`~kiln.config.schema.ActionRouteConfig` in ``config.routes`` one
route file is produced:

- :class:`~kiln.config.schema.ViewRouteConfig` with no parameters → a
  plain SELECT route using a ``text()`` query against the view.
- :class:`~kiln.config.schema.ViewRouteConfig` with parameters → a
  function route using ``func.<schema>.<name>(params).table_valued(cols)``.
- :class:`~kiln.config.schema.ActionRouteConfig` → a POST action route
  calling the named database function.
"""

from __future__ import annotations

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
    from kiln.config.schema import (
        ActionRouteConfig,
        DatabaseConfig,
        KilnConfig,
        ViewConfig,
        ViewRouteConfig,
    )


class ViewGenerator:
    """Generates FastAPI routes for views and set-returning functions."""

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "views"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when the config has at least one view or action route.

        Args:
            config: The validated kiln configuration.

        """
        from kiln.config.schema import (  # noqa: PLC0415
            ActionRouteConfig,
            ViewRouteConfig,
        )

        return any(
            isinstance(r, (ViewRouteConfig, ActionRouteConfig))
            for r in config.routes
        )

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Produce one route file per view or action route in *config*.

        Args:
            config: The validated kiln configuration.

        Returns:
            One :class:`~kiln.generators.base.GeneratedFile` per route,
            written to ``<module>/routes/<name>.py``.

        """
        from kiln.config.schema import (  # noqa: PLC0415
            ActionRouteConfig,
            ViewRouteConfig,
        )

        app = config.module
        view_map = {v.name: v for v in config.views}
        files: list[GeneratedFile] = []

        for route in config.routes:
            if isinstance(route, ViewRouteConfig):
                view = view_map[route.view]
                files.append(
                    GeneratedFile(
                        path=f"{app}/routes/{view.name}.py",
                        content=_render_view_route(
                            route, view, config.module, config.databases
                        ),
                    )
                )
            elif isinstance(route, ActionRouteConfig):
                files.append(
                    GeneratedFile(
                        path=f"{app}/routes/{route.name}.py",
                        content=_render_action_route(
                            route, config.module, config.databases
                        ),
                    )
                )

        return files


# ---------------------------------------------------------------------------
# Route rendering
# ---------------------------------------------------------------------------


def _render_view_route(
    route: ViewRouteConfig,
    view: ViewConfig,
    module: str,
    databases: list[DatabaseConfig],
) -> str:
    """Render the FastAPI route file for a view route.

    Args:
        route: The view route configuration.
        view: The database view configuration.
        module: Root module name for generated import paths.
        databases: The project-level database list.

    Returns:
        Python source string.

    """
    session_module, get_db_fn = resolve_db_session(route.db_key, databases)
    if view.parameters:
        return _render_function_route(
            route, view, module, session_module, get_db_fn
        )
    return _render_plain_route(route, view, module, session_module, get_db_fn)


def _render_plain_route(
    route: ViewRouteConfig,
    view: ViewConfig,
    module: str,
    session_module: str,
    get_db_fn: str,
) -> str:
    """Route that uses a direct SQL text query against the view."""
    col_names_sql = ", ".join(c.name for c in view.returns)
    tmpl = env.get_template("fastapi/view_route_plain.py.j2")
    return tmpl.render(
        view=view,
        module=module,
        result_class=_pascal(view.name) + "Result",
        has_auth=route.require_auth,
        method=route.http_method.lower(),
        slug=view.name.replace("_", "-"),
        imports=type_imports([c.type for c in view.returns]),
        description=route.description or f"Query the {view.name} view.",
        session_module=session_module,
        get_db_fn=get_db_fn,
        col_names_sql=col_names_sql,
        columns=[
            {"name": c.name, "py_type": PYTHON_TYPES[c.type]}
            for c in view.returns
        ],
    )


def _render_function_route(
    route: ViewRouteConfig,
    view: ViewConfig,
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
        has_auth=route.require_auth,
        method=route.http_method.lower(),
        slug=view.name.replace("_", "-"),
        imports=type_imports([c.type for c in all_items]),
        description=route.description or f"Call the {view.name} function.",
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


def _render_action_route(
    action: ActionRouteConfig,
    module: str,
    databases: list[DatabaseConfig],
) -> str:
    """Render the FastAPI route file for an action route.

    Args:
        action: The action route configuration.
        module: Root module name for generated import paths.
        databases: The project-level database list.

    Returns:
        Python source string.

    """
    session_module, get_db_fn = resolve_db_session(action.db_key, databases)
    fn_schema, fn_name = action.fn.rsplit(".", 1)
    all_items = list(action.params) + list(action.returns)
    tv_cols = ", ".join(
        f'column("{c.name}", {SA_INSTANCE_TYPES[c.type]})'
        for c in action.returns
    )
    col_names_str = ", ".join(f'"{c.name}"' for c in action.returns)
    fn_args = ", ".join(f"body.{p.name}" for p in action.params)

    tmpl = env.get_template("fastapi/action_route.py.j2")
    return tmpl.render(
        action=action,
        module=module,
        request_class=_pascal(action.name) + "Request",
        result_class=_pascal(action.name) + "Result",
        has_auth=action.require_auth,
        slug=action.name.replace("_", "-"),
        imports=type_imports([c.type for c in all_items]),
        description=action.description or f"Execute the {action.name} action.",
        session_module=session_module,
        get_db_fn=get_db_fn,
        fn_schema=fn_schema,
        fn_name=fn_name,
        fn_args=fn_args,
        params=[
            {"name": p.name, "py_type": PYTHON_TYPES[p.type]}
            for p in action.params
        ],
        columns=[
            {"name": c.name, "py_type": PYTHON_TYPES[c.type]}
            for c in action.returns
        ],
        tv_cols=tv_cols,
        col_names_str=col_names_str,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pascal(name: str) -> str:
    """Convert snake_case *name* to PascalCase."""
    return "".join(part.capitalize() for part in name.split("_"))
