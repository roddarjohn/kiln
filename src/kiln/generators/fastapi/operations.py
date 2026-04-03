"""Pluggable CRUD operations for the resource pipeline.

Each operation class contributes to a ``dict[str, FileSpec]`` bag,
adding imports, schema classes, route handlers, or entirely new
files.  Extensions can add, replace, or remove operations to
customize the generated output.

Example — adding a custom operation::

    from kiln.generators.fastapi.operations import (
        Operation,
        default_operations,
    )
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    class BulkCreateOperation:
        name = "bulk_create"

        def enabled(self, resource):
            return resource.create is not False

        def contribute(self, specs, resource, ctx):
            schema = specs["schema"]
            route = specs["route"]
            ...

    pipeline = ResourcePipeline(
        operations=[*default_operations(), BulkCreateOperation()]
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from kiln.config.schema import FieldsConfig
from kiln.generators._env import render_snippet
from kiln.generators._helpers import (
    PYTHON_TYPES,
    ImportCollector,
    Name,
    prefix_import,
    resolve_db_session,
)
from kiln.generators.base import FileSpec

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig, ResourceConfig


# -------------------------------------------------------------------
# Shared context
# -------------------------------------------------------------------


@dataclass
class SharedContext:
    """Shared state passed to every operation.

    Contains the resolved values that most operations need but
    that come from the overall resource/config, not from a
    single operation.
    """

    model: Name
    model_module: str
    pk_name: str
    pk_py_type: str
    route_prefix: str
    has_auth: bool
    get_db_fn: str
    session_module: str
    has_resource_schema: bool
    response_schema: str | None
    package_prefix: str


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _field_dicts(fields: list) -> list[dict[str, str]]:
    """Convert FieldSpec list to template-ready dicts."""
    return [{"name": f.name, "py_type": PYTHON_TYPES[f.type]} for f in fields]


def _add_field_type_imports(imports: ImportCollector, fields: list) -> None:
    """Add type-specific imports for *fields* to *imports*."""
    for f in fields:
        ft = f.type
        if ft == "uuid":
            imports.add("uuid")
        elif ft == "datetime":
            imports.add_from("datetime", "datetime")
        elif ft == "date":
            imports.add_from("datetime", "date")
        elif ft == "json":
            imports.add_from("typing", "Any")


def _op_requires_auth(resource: ResourceConfig, op_name: str) -> bool:
    """Return whether *op_name* requires authentication."""
    if isinstance(resource.require_auth, bool):
        return resource.require_auth
    return op_name in resource.require_auth


def _will_have_resource_schema(
    resource: ResourceConfig,
) -> bool:
    """Check if get or list have explicit fields."""
    return isinstance(resource.get, FieldsConfig) or isinstance(
        resource.list, FieldsConfig
    )


def build_shared_context(
    resource: ResourceConfig,
    config: KilnConfig,
) -> SharedContext:
    """Build the :class:`SharedContext` for a resource.

    Args:
        resource: The resource configuration.
        config: The top-level kiln configuration.

    Returns:
        A populated :class:`SharedContext`.

    """
    model_module, model = Name.from_dotted(resource.model)
    session_module, get_db_fn = resolve_db_session(
        resource.db_key, config.databases
    )
    route_prefix = resource.route_prefix or f"/{model.lower}s"
    has_resource_schema = _will_have_resource_schema(resource)
    response_schema = (
        model.suffixed("Resource") if has_resource_schema else None
    )
    return SharedContext(
        model=model,
        model_module=model_module,
        pk_name=resource.pk,
        pk_py_type=PYTHON_TYPES[resource.pk_type],
        route_prefix=route_prefix,
        has_auth=config.auth is not None,
        get_db_fn=get_db_fn,
        session_module=session_module,
        has_resource_schema=has_resource_schema,
        response_schema=response_schema,
        package_prefix=config.package_prefix,
    )


# -------------------------------------------------------------------
# Operation protocol
# -------------------------------------------------------------------


@runtime_checkable
class Operation(Protocol):
    """Protocol for pluggable pipeline operations.

    Each operation receives the full ``specs`` dict and can
    create, read, or modify any :class:`FileSpec` by key.
    Built-in keys are ``"schema"`` and ``"route"``; extensions
    may add others (e.g. ``"test"``, ``"client"``).
    """

    name: str

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True if this operation applies to *resource*."""
        ...

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Add content to *specs* for this operation."""
        ...


# -------------------------------------------------------------------
# Built-in operations
# -------------------------------------------------------------------


class SetupOperation:
    """Creates the base FileSpec objects for schema and route.

    This operation runs first and initialises the ``"schema"``
    and ``"route"`` specs that other operations contribute to.
    When the resource has explicit fields on get or list, a
    ``"serializer"`` spec is also created.
    """

    name = "setup"

    def enabled(
        self,
        resource: ResourceConfig,  # noqa: ARG002
    ) -> bool:
        """Return True unconditionally."""
        return True

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Create base schema, route, and optional serializer."""
        pkg = ctx.package_prefix

        # Derive app from the file paths (model module parent)
        parts = ctx.model_module.rsplit(".", 1)
        app = parts[0] if len(parts) > 1 else ctx.model_module

        # Schema spec
        specs["schema"] = _make_schema_spec(ctx.model, app, pkg)

        # Route spec
        specs["route"] = _make_route_spec(ctx.model, app, pkg, ctx)

        # Serializer spec (only when resource schema exists)
        if ctx.has_resource_schema:
            specs["serializer"] = _make_serializer_spec(
                ctx.model,
                ctx.model_module,
                app,
                pkg,
                resource,
            )


class GetOperation:
    """GET /{pk} — retrieve a single resource by primary key."""

    name = "get"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when get is not disabled."""
        return resource.get is not False

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit schema and route for GET /{pk}."""
        schema = specs["schema"]
        route = specs["route"]

        # Schema contribution
        if isinstance(resource.get, FieldsConfig):
            fields = _field_dicts(resource.get.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/resource.py.j2",
                model_name=ctx.model.pascal,
                fields=fields,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("Resource"))
            _add_field_type_imports(schema.imports, resource.get.fields)

        # Route contribution
        route.imports.add_from("sqlalchemy", "select")
        route.imports.add_from(ctx.model_module, ctx.model.pascal)
        route.imports.add_from(
            route.context["utils_module"],
            "get_object_from_query_or_404",
        )
        handler = render_snippet(
            "fastapi/ops/get.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            pk_py_type=ctx.pk_py_type,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "get"),
            has_resource_schema=ctx.has_resource_schema,
        )
        route.context["route_handlers"].append(handler)


class ListOperation:
    """GET / — list all resources."""

    name = "list"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when list is not disabled."""
        return resource.list is not False

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit schema and route for GET /."""
        schema = specs["schema"]
        route = specs["route"]

        # Schema contribution (only if get didn't already)
        if (
            isinstance(resource.list, FieldsConfig)
            and ctx.model.suffixed("Resource") not in schema.exports
        ):
            fields = _field_dicts(resource.list.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/resource.py.j2",
                model_name=ctx.model.pascal,
                fields=fields,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("Resource"))
            _add_field_type_imports(schema.imports, resource.list.fields)

        # Route contribution
        route.imports.add_from("sqlalchemy", "select")
        route.imports.add_from(ctx.model_module, ctx.model.pascal)
        handler = render_snippet(
            "fastapi/ops/list.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "list"),
            has_resource_schema=ctx.has_resource_schema,
        )
        route.context["route_handlers"].append(handler)


class CreateOperation:
    """POST / — create a new resource."""

    name = "create"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when create is not disabled."""
        return resource.create is not False

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit schema and route for POST /."""
        schema = specs["schema"]
        route = specs["route"]

        # Schema contribution
        if isinstance(resource.create, FieldsConfig):
            fields = _field_dicts(resource.create.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/create.py.j2",
                model_name=ctx.model.pascal,
                route_prefix=ctx.route_prefix,
                fields=fields,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("CreateRequest"))
            _add_field_type_imports(schema.imports, resource.create.fields)

        # Route contribution
        route.imports.add_from("sqlalchemy", "insert")
        route.imports.add_from(ctx.model_module, ctx.model.pascal)
        handler = render_snippet(
            "fastapi/ops/create.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "create"),
            has_schema=isinstance(resource.create, FieldsConfig),
            response_schema=ctx.response_schema,
        )
        route.context["route_handlers"].append(handler)


class UpdateOperation:
    """PATCH /{pk} — partially update a resource."""

    name = "update"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when update is not disabled."""
        return resource.update is not False

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit schema and route for PATCH /{pk}."""
        schema = specs["schema"]
        route = specs["route"]

        # Schema contribution
        if isinstance(resource.update, FieldsConfig):
            fields = _field_dicts(resource.update.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/update.py.j2",
                model_name=ctx.model.pascal,
                route_prefix=ctx.route_prefix,
                fields=fields,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("UpdateRequest"))
            _add_field_type_imports(schema.imports, resource.update.fields)

        # Route contribution
        route.imports.add_from("sqlalchemy", "update")
        route.imports.add_from(ctx.model_module, ctx.model.pascal)
        route.imports.add_from(
            route.context["utils_module"],
            "assert_rowcount",
        )
        handler = render_snippet(
            "fastapi/ops/update.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            pk_py_type=ctx.pk_py_type,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "update"),
            has_schema=isinstance(resource.update, FieldsConfig),
            response_schema=ctx.response_schema,
        )
        route.context["route_handlers"].append(handler)


class DeleteOperation:
    """DELETE /{pk} — delete a resource."""

    name = "delete"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when delete is enabled."""
        return resource.delete

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit the DELETE /{pk} handler."""
        route = specs["route"]
        route.imports.add_from("sqlalchemy", "delete")
        route.imports.add_from(ctx.model_module, ctx.model.pascal)
        route.imports.add_from(
            route.context["utils_module"],
            "assert_rowcount",
        )
        handler = render_snippet(
            "fastapi/ops/delete.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            pk_py_type=ctx.pk_py_type,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "delete"),
        )
        route.context["route_handlers"].append(handler)


class ActionOperation:
    """POST /{pk}/{action_slug} — custom action endpoints."""

    name = "actions"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when the resource has actions."""
        return bool(resource.actions)

    def contribute(
        self,
        specs: dict[str, FileSpec],
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit action schemas and route handlers."""
        schema = specs["schema"]
        route = specs["route"]

        # Schema contributions
        for action in resource.actions:
            action_name = Name(action.name)
            if action.params:
                fields = _field_dicts(action.params)
                snippet = render_snippet(
                    "fastapi/schema_parts/action_request.py.j2",
                    request_class=action_name.suffixed("Request"),
                    route_prefix=ctx.route_prefix,
                    slug=action_name.slug,
                    params=fields,
                )
                schema.context["schema_classes"].append(snippet)
                schema.exports.append(action_name.suffixed("Request"))
                _add_field_type_imports(schema.imports, action.params)

        snippet = render_snippet(
            "fastapi/schema_parts/action_response.py.j2",
        )
        schema.context["schema_classes"].append(snippet)
        schema.exports.append("ActionResponse")

        # Route contributions
        for action in resource.actions:
            action_name = Name(action.name)
            fn_module, fn_name = Name.from_dotted(action.fn)
            route.imports.add_from(fn_module, fn_name.raw)

            action_ctx = {
                "name": action_name.raw,
                "fn_name": fn_name.raw,
                "slug": action_name.slug,
                "handler_name": f"{action_name.raw}_action",
                "request_class": action_name.suffixed("Request"),
                "params": _field_dicts(action.params),
                "requires_auth": action.require_auth,
            }
            handler = render_snippet(
                "fastapi/ops/action.py.j2",
                action=action_ctx,
                model_name=ctx.model.pascal,
                pk_name=ctx.pk_name,
                pk_py_type=ctx.pk_py_type,
                get_db_fn=ctx.get_db_fn,
                has_auth=ctx.has_auth,
            )
            route.context["route_handlers"].append(handler)


# -------------------------------------------------------------------
# FileSpec factories (used by SetupOperation)
# -------------------------------------------------------------------


def _make_schema_spec(model: Name, app: str, pkg: str) -> FileSpec:
    """Create the schema FileSpec with base imports."""
    spec = FileSpec(
        path=f"{app}/schemas/{model.lower}.py",
        template="fastapi/schema_outer.py.j2",
        imports=ImportCollector(),
        package_prefix=pkg,
        context={
            "model_name": model.pascal,
            "schema_classes": [],
        },
    )
    spec.imports.add_from("__future__", "annotations")
    spec.imports.add_from("pydantic", "BaseModel")
    return spec


def _make_serializer_spec(
    model: Name,
    model_module: str,
    app: str,
    pkg: str,
    resource: ResourceConfig,
) -> FileSpec:
    """Create the serializer FileSpec with base imports."""
    resource_fields: list[dict[str, str]] = []
    if isinstance(resource.get, FieldsConfig):
        resource_fields = _field_dicts(resource.get.fields)
    elif isinstance(resource.list, FieldsConfig):
        resource_fields = _field_dicts(resource.list.fields)

    spec = FileSpec(
        path=f"{app}/serializers/{model.lower}.py",
        template="fastapi/serializer_outer.py.j2",
        imports=ImportCollector(),
        exports=[f"to_{model.lower}_resource"],
        package_prefix=pkg,
        context={
            "model_name": model.pascal,
            "model_lower": model.lower,
            "resource_fields": resource_fields,
        },
    )
    spec.imports.add_from("__future__", "annotations")
    spec.imports.add_from(model_module, model.pascal)
    return spec


def _make_route_spec(
    model: Name,
    app: str,
    pkg: str,
    ctx: SharedContext,
) -> FileSpec:
    """Create the route FileSpec with base imports."""
    utils_module = prefix_import(pkg, "utils")
    spec = FileSpec(
        path=f"{app}/routes/{model.lower}.py",
        template="fastapi/route.py.j2",
        imports=ImportCollector(),
        package_prefix=pkg,
        context={
            "model_name": model.pascal,
            "model_lower": model.lower,
            "route_prefix": ctx.route_prefix,
            "route_handlers": [],
            "utils_module": utils_module,
        },
    )
    spec.imports.add_from("__future__", "annotations")

    if "uuid" in ctx.pk_py_type:
        spec.imports.add("uuid")

    spec.imports.add_from("typing", "Annotated")
    spec.imports.add_from("fastapi", "APIRouter", "Depends", "status")
    spec.imports.add_from("sqlalchemy.ext.asyncio", "AsyncSession")

    if ctx.has_auth:
        spec.imports.add_from("auth.dependencies", "get_current_user")

    spec.imports.add_from(ctx.session_module, ctx.get_db_fn)
    return spec


def default_operations() -> list[Operation]:
    """Return the default list of built-in operations.

    The list always starts with :class:`SetupOperation` which
    creates the base ``"schema"`` and ``"route"`` specs.
    Extensions can append to or modify this list::

        ops = default_operations()
        ops.append(MyCustomOperation())

    Returns:
        Ordered list of operation instances.

    """
    return [
        SetupOperation(),
        GetOperation(),
        ListOperation(),
        CreateOperation(),
        UpdateOperation(),
        DeleteOperation(),
        ActionOperation(),
    ]
