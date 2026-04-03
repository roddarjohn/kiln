"""Generator that produces FastAPI schema and route files for resources."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from kiln.config.schema import FieldsConfig
from kiln.generators._env import env
from kiln.generators._helpers import (
    PYTHON_TYPES,
    prefix_import,
    prefix_path,
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
    """Build the template context dict for a single CRUD operation.

    When *op_value* is a :class:`~kiln.config.schema.FieldsConfig`, the op
    has an explicit schema (``has_schema=True``) and exposes the field list
    for code generation.  When it is the boolean ``True``, the endpoint is
    enabled but no schema is generated (``has_schema=False``).
    """
    if isinstance(op_value, FieldsConfig):
        return {
            "enabled": True,
            "has_schema": True,
            "fields": _field_dicts(op_value.fields),
            "sa_columns": _sa_columns(op_value.fields, model_name),
            "requires_auth": _op_requires_auth(require_auth, op_name),
        }
    return {
        "enabled": True,
        "has_schema": False,
        "fields": [],
        "sa_columns": [],
        "requires_auth": _op_requires_auth(require_auth, op_name),
    }


def _disabled_op() -> dict:
    return {
        "enabled": False,
        "has_schema": False,
        "fields": [],
        "sa_columns": [],
        "requires_auth": False,
    }


def _build_op_ctx(
    resource: ResourceConfig,
    op_name: str,
    require_auth: Sequence[str] | bool,  # noqa: FBT001
    model_name: str,
) -> dict:
    """Return the op context for *op_name*, dispatching to disabled/enabled."""
    op_value = getattr(resource, op_name)
    if op_value is False:
        return _disabled_op()
    return _op_ctx(op_value, op_name, require_auth, model_name)


def _collect_schema_types(
    resource: ResourceConfig, ops: dict[str, dict]
) -> list[str]:
    """Collect field type strings needed for schema file imports.

    Args:
        resource: The resource configuration (for field access and actions).
        ops: Mapping of op name → op context dict (get/list/create/update).

    Returns:
        List of :data:`FieldType` strings for explicit fields and action params.

    """
    types: list[str] = []
    for op_name, op_ctx in ops.items():
        if op_ctx["enabled"] and op_ctx["has_schema"]:
            op_value = getattr(resource, op_name)
            if isinstance(op_value, FieldsConfig):
                types.extend(f.type for f in op_value.fields)
    for action in resource.actions:
        types.extend(p.type for p in action.params)
    return types


def _collect_schema_names(
    model_name: str, ops: dict[str, dict], action_ctxs: list[dict]
) -> list[str]:
    """Collect names exported from the schema module for route imports.

    Args:
        model_name: PascalCase model class name.
        ops: Mapping of op name → op context dict (get/list/create/update).
        action_ctxs: Rendered action context dicts.

    Returns:
        List of class name strings to import from the schema module.

    """
    names: list[str] = []
    if ops["get"]["enabled"] and ops["get"]["has_schema"]:
        names.append(f"{model_name}GetResponse")
    if ops["list"]["enabled"] and ops["list"]["has_schema"]:
        names.append(f"{model_name}ListResponse")
    if ops["create"]["enabled"] and ops["create"]["has_schema"]:
        names.append(f"{model_name}CreateRequest")
    if ops["update"]["enabled"] and ops["update"]["has_schema"]:
        names.append(f"{model_name}UpdateRequest")
    names.extend(a["request_class"] for a in action_ctxs if a["params"])
    if action_ctxs:
        names.append("ActionResponse")
    return names


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
    """Produces one schema file and one route file per resource in config.

    For each resource two files are emitted:

    * ``{module}/schemas/{model}.py`` — explicit Pydantic request/response
      schemas with named fields.
    * ``{module}/routes/{model}.py`` — async FastAPI route handlers using
      raw SQLAlchemy Core statements (``select``, ``insert``, ``update``,
      ``delete``).  Routes import schemas from the schema module and return
      schema instances via generated serializer functions.

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
        """Generate one schema file and one route file per resource.

        Args:
            config: The validated kiln configuration.

        Returns:
            Two :class:`~kiln.generators.base.GeneratedFile` instances per
            resource — a schema file under ``{module}/schemas/`` and a route
            file under ``{module}/routes/``.

        """
        pkg = config.package_prefix
        app = config.module
        files: list[GeneratedFile] = []
        for resource in config.resources:
            session_module, get_db_fn = resolve_db_session(
                resource.db_key, config.databases
            )
            ctx = _build_ctx(resource, config, session_module, get_db_fn)
            model_lower = ctx["model_lower"]

            files.append(
                GeneratedFile(
                    path=prefix_path(pkg, app, "schemas", f"{model_lower}.py"),
                    content=_render_schema(ctx),
                )
            )
            files.append(
                GeneratedFile(
                    path=prefix_path(pkg, app, "routes", f"{model_lower}.py"),
                    content=_render_resource(ctx),
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


def _sqlalchemy_imports(
    op_get: dict,
    op_list: dict,
    op_create: dict,
    op_update: dict,
    op_delete: dict,
) -> list[str]:
    """Return the SQLAlchemy statement names needed in the route file."""
    ops = []
    if op_get["enabled"] or op_list["enabled"]:
        ops.append("select")
    if op_create["enabled"]:
        ops.append("insert")
    if op_update["enabled"]:
        ops.append("update")
    if op_delete["enabled"]:
        ops.append("delete")
    return ops


def _action_imports(action_ctxs: list[dict]) -> list[str]:
    """Return top-level import lines for all action callables.

    Groups callables by module so that multiple actions from the same module
    produce a single ``from module import fn1, fn2`` line.

    Args:
        action_ctxs: Rendered action context dicts (from :func:`_action_ctx`).

    Returns:
        List of ``from module import name[, name…]`` strings.

    """
    by_module: dict[str, list[str]] = defaultdict(list)
    for a in action_ctxs:
        by_module[a["fn_module"]].append(a["fn_name"])
    return [
        f"from {mod} import {', '.join(fns)}" for mod, fns in by_module.items()
    ]


def _serializer_ctxs(
    model_name: str, model_lower: str, op_get: dict, op_list: dict
) -> list[dict]:
    """Build context dicts for the route file's serializer functions.

    A serializer is a small helper that explicitly constructs a schema
    instance from an ORM object or SQLAlchemy ``Row``.

    Args:
        model_name: PascalCase model class name.
        model_lower: Lowercase model name used in function names.
        op_get: Rendered get op context.
        op_list: Rendered list op context.

    Returns:
        List of dicts consumed by the serializer section of the route template.

    """
    serializers = []
    if op_get["enabled"] and op_get["has_schema"]:
        serializers.append(
            {
                "name": f"_to_{model_lower}_get",
                "schema_class": f"{model_name}GetResponse",
                "fields": op_get["fields"],
            }
        )
    if op_list["enabled"] and op_list["has_schema"]:
        serializers.append(
            {
                "name": f"_to_{model_lower}_list",
                "schema_class": f"{model_name}ListResponse",
                "fields": op_list["fields"],
            }
        )
    return serializers


def _build_ctx(
    resource: ResourceConfig,
    config: KilnConfig,
    session_module: str,
    get_db_fn: str,
) -> dict:
    """Build the shared template context for both schema and route files.

    Args:
        resource: The resource configuration.
        config: The top-level kiln configuration.
        session_module: Dotted module for the DB session.
        get_db_fn: Name of the session dependency function.

    Returns:
        Dict of template variables consumed by both ``schema.py.j2`` and
        ``resource.py.j2``.

    """
    model_module, model_name = split_dotted_class(resource.model)
    model_lower = model_name.lower()
    app = config.module
    pkg = config.package_prefix

    route_prefix = resource.route_prefix or f"/{model_lower}s"
    require_auth = resource.require_auth

    op_get = _build_op_ctx(resource, "get", require_auth, model_name)
    op_list = _build_op_ctx(resource, "list", require_auth, model_name)
    op_create = _build_op_ctx(resource, "create", require_auth, model_name)
    op_update = _build_op_ctx(resource, "update", require_auth, model_name)
    op_delete = {
        "enabled": resource.delete,
        "has_schema": False,
        "requires_auth": _op_requires_auth(require_auth, "delete"),
        "fields": [],
        "sa_columns": [],
    }

    action_ctxs = [_action_ctx(a) for a in resource.actions]
    has_actions = bool(action_ctxs)

    read_write_ops = {
        "get": op_get,
        "list": op_list,
        "create": op_create,
        "update": op_update,
    }
    schema_types = _collect_schema_types(resource, read_write_ops)
    schema_names = _collect_schema_names(
        model_name, read_write_ops, action_ctxs
    )

    pk_types: list[str] = [resource.pk_type]

    serializers = _serializer_ctxs(model_name, model_lower, op_get, op_list)
    sa_ops = _sqlalchemy_imports(
        op_get, op_list, op_create, op_update, op_delete
    )
    act_imports = _action_imports(action_ctxs)

    # Response schema for create/update: reuse the get schema when available.
    response_schema = (
        f"{model_name}GetResponse"
        if op_get["enabled"] and op_get["has_schema"]
        else None
    )

    uses_utils = op_get["enabled"] and op_get["has_schema"]
    utils_module = prefix_import(pkg, app, "utils")
    schema_module = prefix_import(pkg, app, "schemas", model_lower)

    return {
        "model_name": model_name,
        "model_module": model_module,
        "model_lower": model_lower,
        "route_prefix": route_prefix,
        "has_auth": config.auth is not None,
        "session_module": session_module,
        "get_db_fn": get_db_fn,
        "pk_name": resource.pk,
        "pk_py_type": PYTHON_TYPES[resource.pk_type],
        "schema_imports": type_imports(schema_types),
        "pk_imports": type_imports(pk_types),
        "op_get": op_get,
        "op_list": op_list,
        "op_create": op_create,
        "op_update": op_update,
        "op_delete": op_delete,
        "actions": action_ctxs,
        "has_actions": has_actions,
        "response_schema": response_schema,
        "schema_module": schema_module,
        "utils_module": utils_module,
        "uses_utils": uses_utils,
        "schema_names": schema_names,
        "serializers": serializers,
        "sqlalchemy_imports": sa_ops,
        "action_imports": act_imports,
    }


def _render_schema(ctx: dict) -> str:
    """Render the schema file for a resource.

    Args:
        ctx: The shared template context from :func:`_build_ctx`.

    Returns:
        Python source string for the schema module.

    """
    tmpl = env.get_template("fastapi/schema.py.j2")
    return tmpl.render(**ctx)


def _render_resource(ctx: dict) -> str:
    """Render the route file for a resource.

    Args:
        ctx: The shared template context from :func:`_build_ctx`.

    Returns:
        Python source string for the route module.

    """
    tmpl = env.get_template("fastapi/resource.py.j2")
    return tmpl.render(**ctx)
