"""Generator that produces FastAPI route files for resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.config.schema import FieldsConfig
from kiln.generators._env import env
from kiln.generators._helpers import (
    PYTHON_TYPES,
    resolve_db_session,
    split_dotted_class,
    type_imports,
)
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from collections.abc import Sequence

    from kiln.config.schema import (
        ActionConfig,
        FieldSpec,
        KilnConfig,
        ResourceConfig,
    )


def _pascal(name: str) -> str:
    """Convert snake_case *name* to PascalCase."""
    return "".join(part.capitalize() for part in name.split("_"))


def _op_requires_auth(
    require_auth: Sequence[str] | bool,  # noqa: FBT001
    op_name: str,
) -> bool:
    """Return whether *op_name* requires authentication."""
    if isinstance(require_auth, bool):
        return require_auth
    return op_name in require_auth


def _field_dicts(fields: list[FieldSpec]) -> list[dict[str, str]]:
    """Convert FieldSpec list to template-ready dicts with py_type."""
    return [{"name": f.name, "py_type": PYTHON_TYPES[f.type]} for f in fields]


def _sa_columns(fields: list[FieldSpec], model_name: str) -> list[str]:
    """Return column expression strings for a specific-fields select.

    Each entry is rendered verbatim into the generated
    ``select(...)`` call, e.g. ``["User.id", "User.email"]``.
    """
    return [f"{model_name}.{f.name}" for f in fields]


def _op_ctx(
    op_value: bool | FieldsConfig,  # noqa: FBT001
    op_name: str,
    require_auth: Sequence[str] | bool,  # noqa: FBT001
    model_name: str,
) -> dict:
    """Build the template context dict for a single CRUD operation."""
    if isinstance(op_value, FieldsConfig):
        all_fields = False
        fields: list[FieldSpec] = op_value.fields
    else:
        all_fields = True
        fields = []
    return {
        "enabled": True,
        "all_fields": all_fields,
        "fields": _field_dicts(fields),
        "sa_columns": _sa_columns(fields, model_name),
        "requires_auth": _op_requires_auth(require_auth, op_name),
    }


def _disabled_op() -> dict:
    return {
        "enabled": False,
        "all_fields": False,
        "fields": [],
        "sa_columns": [],
        "requires_auth": False,
    }


def _action_ctx(action: ActionConfig) -> dict:
    """Build the template context dict for an action."""
    fn_module, fn_name = split_dotted_class(action.fn)
    return {
        "name": action.name,
        "fn_module": fn_module,
        "fn_name": fn_name,
        "slug": action.name.replace("_", "-"),
        "handler_name": f"{action.name}_action",
        "request_class": _pascal(action.name) + "Request",
        "params": _field_dicts(action.params),
        "requires_auth": action.require_auth,
    }


class ResourceGenerator:
    """Produces one FastAPI route file per resource in config.

    Each file contains:

    * Pydantic request/response schemas (static for explicit field lists,
      dynamically built via SQLAlchemy inspection for ``True`` ops).
    * Async route handlers for each enabled CRUD operation.
    * Action endpoints that delegate to Python callables or Postgres
      functions.

    Generated files are always overwritten on re-generation.
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "resources"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when the config defines at least one resource.

        Args:
            config: The validated kiln configuration.

        """
        return bool(config.resources)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate one route file per resource.

        Args:
            config: The validated kiln configuration.

        Returns:
            One :class:`~kiln.generators.base.GeneratedFile` per resource,
            written to ``{module}/routes/{model_lower}.py``.

        """
        app = config.module
        files: list[GeneratedFile] = []
        for resource in config.resources:
            session_module, get_db_fn = resolve_db_session(
                resource.db_key, config.databases
            )
            files.append(
                GeneratedFile(
                    path=f"{app}/routes/{_model_lower(resource)}.py",
                    content=_render_resource(
                        resource, config, session_module, get_db_fn
                    ),
                )
            )
        return files


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _model_lower(resource: ResourceConfig) -> str:
    """Derive a snake_case module name from the model's class name."""
    _, class_name = split_dotted_class(resource.model)
    return class_name.lower()


def _render_resource(
    resource: ResourceConfig,
    config: KilnConfig,
    session_module: str,
    get_db_fn: str,
) -> str:
    """Render the full route file for *resource*.

    Args:
        resource: The resource configuration.
        config: The top-level kiln configuration (for auth presence).
        session_module: Dotted module for the DB session, e.g.
            ``"db.session"``.
        get_db_fn: Name of the session dependency, e.g. ``"get_db"``.

    Returns:
        Python source string.

    """
    model_module, model_name = split_dotted_class(resource.model)
    model_lower = model_name.lower()

    # Determine the URL prefix.
    route_prefix = resource.route_prefix or f"/{model_lower}s"

    # Build per-op context. Use prefixed names (op_get, op_list, …) to
    # avoid passing 'get' or 'list' as Jinja2 variable names — those
    # clash with Python dict methods during attribute lookup in templates.
    require_auth = resource.require_auth

    def _build_op(op_name: str) -> dict:
        op_value = getattr(resource, op_name)
        if op_value is False:
            return _disabled_op()
        return _op_ctx(op_value, op_name, require_auth, model_name)

    op_get = _build_op("get")
    op_list = _build_op("list")
    op_create = _build_op("create")
    op_update = _build_op("update")
    op_delete = {
        "enabled": resource.delete,
        "requires_auth": _op_requires_auth(require_auth, "delete"),
        "all_fields": False,
        "fields": [],
        "sa_columns": [],
    }

    # Actions.
    action_ctxs = [_action_ctx(a) for a in resource.actions]

    # Response schema used for create/update — prefer the get schema.
    response_schema = (
        f"{model_name}GetResponse" if op_get["enabled"] else "object"
    )

    # Decide whether to emit _build_schema helper.
    needs_build_schema = (op_get["enabled"] and op_get["all_fields"]) or (
        op_list["enabled"] and op_list["all_fields"]
    )

    # Decide whether to emit `from sqlalchemy import select`.
    needs_select = op_get["enabled"] or op_list["enabled"]

    # Whether to emit _get_or_404 helper.
    needs_get_or_404 = (
        (op_get["enabled"] and op_get["all_fields"])
        or op_update["enabled"]
        or op_delete["enabled"]
    )

    # Collect all field types for imports.
    all_types: list[str] = [resource.pk_type]
    for op_name, op_ctx in (
        ("get", op_get),
        ("list", op_list),
        ("create", op_create),
        ("update", op_update),
    ):
        if op_ctx["enabled"] and not op_ctx["all_fields"]:
            op_value = getattr(resource, op_name)
            if isinstance(op_value, FieldsConfig):
                all_types.extend(f.type for f in op_value.fields)
    for action in resource.actions:
        all_types.extend(p.type for p in action.params)

    tmpl = env.get_template("fastapi/resource.py.j2")
    return tmpl.render(
        model_name=model_name,
        model_module=model_module,
        model_lower=model_lower,
        route_prefix=route_prefix,
        has_auth=config.auth is not None,
        session_module=session_module,
        get_db_fn=get_db_fn,
        pk_name=resource.pk,
        pk_py_type=PYTHON_TYPES[resource.pk_type],
        imports=type_imports(all_types),
        needs_build_schema=needs_build_schema,
        needs_select=needs_select,
        needs_get_or_404=needs_get_or_404,
        op_get=op_get,
        op_list=op_list,
        op_create=op_create,
        op_update=op_update,
        op_delete=op_delete,
        actions=action_ctxs,
        response_schema=response_schema,
    )
