"""Pluggable CRUD operations for the resource pipeline.

Each operation class contributes to a ``dict[str, FileSpec]`` bag,
adding imports, schema classes, route handlers, or entirely new
files.  Extensions can add, replace, or remove operations to
customize the generated output.

Operations are discovered via the ``kiln.operations`` entry-point
group.  kiln registers its own built-in operations in
``pyproject.toml``; third-party packages do the same::

    # pyproject.toml
    [project.entry-points."kiln.operations"]
    bulk_create = "my_package.ops:BulkCreateOperation"

Operations can also be referenced by dotted class path in the
config, or via an explicit ``class`` key in operation options.

Each operation defines an ``Options`` inner class (a Pydantic
model) that declares and validates its configuration.  The
pipeline parses options via Pydantic before calling
``contribute()``, so operations receive typed, validated data.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel

from kiln.config.schema import FieldSpec  # noqa: TC001
from kiln.generators._env import render_snippet
from kiln.generators._helpers import (
    PYTHON_TYPES,
    ImportCollector,
    Name,
    prefix_import,
    resolve_db_session,
)
from kiln.generators.base import FileSpec as FileSpecType
from kiln.generators.fastapi.list_extensions import (
    FilterConfig,
    OrderConfig,
    PaginateConfig,
    contribute_filters,
    contribute_ordering,
    contribute_pagination,
)

if TYPE_CHECKING:
    from typing import Any

    from kiln.config.schema import (
        KilnConfig,
        OperationConfig,
        ResourceConfig,
    )


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


def _field_dicts(fields: list[FieldSpec]) -> list[dict[str, str]]:
    """Convert :class:`FieldSpec` list to template-ready dicts."""
    return [{"name": f.name, "py_type": PYTHON_TYPES[f.type]} for f in fields]


def _add_field_type_imports(
    imports: ImportCollector,
    fields: list[FieldSpec],
) -> None:
    """Add type-specific imports for *fields* to *imports*."""
    for f in fields:
        if f.type == "uuid":
            imports.add("uuid")
        elif f.type == "datetime":
            imports.add_from("datetime", "datetime")
        elif f.type == "date":
            imports.add_from("datetime", "date")
        elif f.type == "json":
            imports.add_from("typing", "Any")


def _op_requires_auth(
    resource: ResourceConfig,
    op_config: OperationConfig,
) -> bool:
    """Return whether this operation requires authentication."""
    if op_config.require_auth is not None:
        return op_config.require_auth
    return resource.require_auth


def _will_have_resource_schema(
    op_configs: list[OperationConfig],
) -> bool:
    """Check if any get or list operation has explicit fields."""
    return any(
        oc.name in ("get", "list") and "fields" in oc.options
        for oc in op_configs
    )


def build_shared_context(
    resource: ResourceConfig,
    config: KilnConfig,
    op_configs: list[OperationConfig],
) -> SharedContext:
    """Build the :class:`SharedContext` for a resource.

    Args:
        resource: The resource configuration.
        config: The top-level kiln configuration.
        op_configs: The resolved list of operation configs.

    Returns:
        A populated :class:`SharedContext`.

    """
    model_module, model = Name.from_dotted(resource.model)
    session_module, get_db_fn = resolve_db_session(
        resource.db_key, config.databases
    )
    route_prefix = resource.route_prefix or f"/{model.lower}s"
    has_resource_schema = _will_have_resource_schema(op_configs)
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


class EmptyOptions(BaseModel):
    """Default options model for operations that take no config."""


@runtime_checkable
class Operation(Protocol):
    """Protocol for pluggable pipeline operations.

    Each operation receives the full ``specs`` dict and can
    create, read, or modify any :class:`FileSpec` by key.
    Built-in keys are ``"schema"`` and ``"route"``; extensions
    may add others (e.g. ``"test"``, ``"client"``).

    Operations must define an ``Options`` inner class (a Pydantic
    ``BaseModel``) that declares and validates configuration.
    The pipeline parses ``op_config.options`` via this model and
    passes the result to ``contribute()``.

    Operations are discovered via the ``kiln.operations``
    entry-point group and resolved by name or dotted class path.
    """

    name: str
    Options: type[BaseModel]

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
        options: BaseModel,
    ) -> None:
        """Add content to *specs* for this operation.

        Args:
            specs: Mutable dict of file specs.
            resource: The resource configuration.
            ctx: Shared context for this resource.
            op_config: The operation's configuration entry
                (for ``name`` and ``require_auth``).
            options: Parsed ``Options`` model instance.

        """
        ...


# -------------------------------------------------------------------
# Operation registry
# -------------------------------------------------------------------


class OperationRegistry:
    """Discovers and caches operation classes from entry points.

    Mirrors :class:`~kiln.generators.registry.GeneratorRegistry`
    but for individual pipeline operations.

    Usage::

        registry = OperationRegistry.default()
        op = registry.resolve("get", {})

    Third-party packages register operations via
    ``pyproject.toml``::

        [project.entry-points."kiln.operations"]
        bulk_create = "my_package.ops:BulkCreateOperation"

    """

    def __init__(self) -> None:  # noqa: D107
        self._registry: dict[str, type[Operation]] = {}

    def discover(self) -> None:
        """Load all operations from ``kiln.operations`` entry points."""
        for ep in importlib.metadata.entry_points(
            group="kiln.operations",
        ):
            self._registry[ep.name] = ep.load()

    def register(self, name: str, cls: type[Operation]) -> None:
        """Manually register an operation class.

        Useful for tests that need to register operations without
        entry points.

        Args:
            name: Short name for the operation.
            cls: The operation class.

        """
        self._registry[name] = cls

    def resolve(self, name: str, options: dict[str, Any]) -> Operation:
        """Resolve an operation by name, entry point, or class path.

        Resolution order:

        1. Explicit ``class`` key in *options* — import and
           instantiate the class.
        2. Entry-point registry lookup by *name*.
        3. If *name* is not registered but *options* contains
           ``fn`` — use the ``"action"`` entry point.
        4. Dotted class path fallback — if *name* contains a
           dot, treat it as an importable class.

        Args:
            name: Operation name or dotted class path.
            options: The operation's options dict.

        Returns:
            An instantiated :class:`Operation`.

        Raises:
            ValueError: When the operation cannot be resolved.

        """
        if "class" in options:
            return _import_class(options["class"])()
        if name in self._registry:
            return self._registry[name]()
        if "fn" in options and "action" in self._registry:
            return self._registry["action"]()
        if "." in name:
            return _import_class(name)()
        msg = (
            f"Unknown operation '{name}'. Register it via a "
            f"kiln.operations entry point or use a dotted class "
            f"path."
        )
        raise ValueError(msg)

    @classmethod
    def default(cls) -> OperationRegistry:
        """Return a registry pre-loaded from entry points.

        Returns:
            A ready-to-use :class:`OperationRegistry`.

        """
        registry = cls()
        registry.discover()
        return registry


def _import_class(dotted_path: str) -> type[Operation]:
    """Import a class from a dotted path.

    Args:
        dotted_path: E.g. ``"my_package.ops.BulkCreateOperation"``.

    Returns:
        The imported class.

    """
    module, cls_name = dotted_path.rsplit(".", 1)
    mod = importlib.import_module(module)
    return getattr(mod, cls_name)


# -------------------------------------------------------------------
# Common options models
# -------------------------------------------------------------------


class FieldsOptions(BaseModel):
    """Options for operations that accept an optional field list."""

    fields: list[FieldSpec] | None = None


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
    Options = EmptyOptions

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,  # noqa: ARG002
        ctx: SharedContext,
        op_config: OperationConfig,  # noqa: ARG002
        options: EmptyOptions,  # noqa: ARG002
    ) -> None:
        """Create base schema, route, and optional serializer."""
        pkg = ctx.package_prefix

        # Derive app from the file paths (model module parent)
        parts = ctx.model_module.rsplit(".", 1)
        app = parts[0] if len(parts) > 1 else ctx.model_module

        # Schema spec
        specs["schema"] = _make_schema_spec(ctx.model, app, pkg)

        # Serializer spec (only when resource schema exists)
        if ctx.has_resource_schema:
            specs["serializer"] = _make_serializer_spec(
                ctx.model,
                ctx.model_module,
                app,
                pkg,
            )

        # Route spec
        specs["route"] = _make_route_spec(ctx.model, app, pkg, ctx)

        # Extension points for list operation plugins
        specs["route"].context["list_extensions"] = {
            "extra_params": [],
            "query_modifiers": [],
            "response_model": None,
            "return_type": None,
            "result_expression": None,
        }


class GetOperation:
    """GET /{pk} — retrieve a single resource by primary key."""

    name = "get"
    Options = FieldsOptions

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
        options: FieldsOptions,
    ) -> None:
        """Emit schema and route for GET /{pk}."""
        schema = specs["schema"]
        route = specs["route"]

        # Schema contribution
        if options.fields:
            field_dicts = _field_dicts(options.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/resource.py.j2",
                model_name=ctx.model.pascal,
                fields=field_dicts,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("Resource"))
            _add_field_type_imports(schema.imports, options.fields)
            # Populate serializer fields if present
            if "serializer" in specs:
                serializer = specs["serializer"]
                if not serializer.context["resource_fields"]:
                    serializer.context["resource_fields"] = field_dicts

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
            requires_auth=_op_requires_auth(resource, op_config),
            has_resource_schema=ctx.has_resource_schema,
        )
        route.context["route_handlers"].append(handler)


def _list_response_types(
    ext: dict,
    ctx: SharedContext,
) -> tuple[str, str]:
    """Resolve response_model and return_type for the list handler.

    When a pagination extension sets overrides they take
    precedence; otherwise the default list types are used.

    Args:
        ext: The ``list_extensions`` context dict.
        ctx: Shared context for this resource.

    Returns:
        ``(response_model, return_type)`` tuple.

    """
    response_model = ext.get("response_model")
    return_type = ext.get("return_type")
    if not response_model:
        if ctx.has_resource_schema:
            response_model = f"list[{ctx.model.pascal}Resource]"
        else:
            response_model = "list"
    if not return_type:
        if ctx.has_resource_schema:
            return_type = f"list[{ctx.model.pascal}Resource]"
        else:
            return_type = "object"
    return response_model, return_type


class ListOperation:
    """GET / — list all resources.

    Supports optional filtering, ordering, and pagination via
    sub-configuration keys.  When present, these delegate to
    helper functions in
    :mod:`kiln.generators.fastapi.list_extensions`.
    """

    name = "list"

    class Options(BaseModel):
        """Options for the list operation."""

        fields: list[FieldSpec] | None = None
        filters: FilterConfig | None = None
        ordering: OrderConfig | None = None
        pagination: PaginateConfig | None = None

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
        options: Options,
    ) -> None:
        """Emit schema and route for GET /."""
        schema = specs["schema"]

        # Schema contribution (only if get didn't already)
        if (
            options.fields
            and ctx.model.suffixed("Resource") not in schema.exports
        ):
            field_dicts = _field_dicts(options.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/resource.py.j2",
                model_name=ctx.model.pascal,
                fields=field_dicts,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("Resource"))
            _add_field_type_imports(schema.imports, options.fields)
            # Populate serializer fields if present
            if "serializer" in specs:
                serializer = specs["serializer"]
                if not serializer.context["resource_fields"]:
                    serializer.context["resource_fields"] = field_dicts

        # Delegate to extension helpers (before rendering)
        if options.filters:
            contribute_filters(
                specs,
                ctx,
                options.filters,
                options.fields,
            )
        if options.ordering:
            contribute_ordering(specs, ctx, options.ordering)
        if options.pagination:
            contribute_pagination(specs, ctx, options.pagination)

        # Route contribution
        self._contribute_route(specs, resource, ctx, op_config)

    def _contribute_route(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
    ) -> None:
        """Render the list route handler."""
        route = specs["route"]
        route.imports.add_from("sqlalchemy", "select")
        route.imports.add_from(ctx.model_module, ctx.model.pascal)
        ext = route.context.get("list_extensions", {})
        response_model, return_type = _list_response_types(ext, ctx)
        handler = render_snippet(
            "fastapi/ops/list.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, op_config),
            has_resource_schema=ctx.has_resource_schema,
            response_model=response_model,
            return_type=return_type,
            http_method=ext.get("http_method", "get"),
            route_path=ext.get("route_path", "/"),
            extra_params=ext.get("extra_params", []),
            query_modifiers=ext.get("query_modifiers", []),
            result_expression=ext.get("result_expression"),
        )
        route.context["route_handlers"].append(handler)


class CreateOperation:
    """POST / — create a new resource."""

    name = "create"
    Options = FieldsOptions

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
        options: FieldsOptions,
    ) -> None:
        """Emit schema and route for POST /."""
        schema = specs["schema"]
        route = specs["route"]
        has_schema = bool(options.fields)

        # Schema contribution
        if options.fields:
            field_dicts = _field_dicts(options.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/create.py.j2",
                model_name=ctx.model.pascal,
                route_prefix=ctx.route_prefix,
                fields=field_dicts,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("CreateRequest"))
            _add_field_type_imports(schema.imports, options.fields)

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
            requires_auth=_op_requires_auth(resource, op_config),
            has_schema=has_schema,
            response_schema=ctx.response_schema,
        )
        route.context["route_handlers"].append(handler)


class UpdateOperation:
    """PATCH /{pk} — partially update a resource."""

    name = "update"
    Options = FieldsOptions

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
        options: FieldsOptions,
    ) -> None:
        """Emit schema and route for PATCH /{pk}."""
        schema = specs["schema"]
        route = specs["route"]
        has_schema = bool(options.fields)

        # Schema contribution
        if options.fields:
            field_dicts = _field_dicts(options.fields)
            snippet = render_snippet(
                "fastapi/schema_parts/update.py.j2",
                model_name=ctx.model.pascal,
                route_prefix=ctx.route_prefix,
                fields=field_dicts,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(ctx.model.suffixed("UpdateRequest"))
            _add_field_type_imports(schema.imports, options.fields)

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
            requires_auth=_op_requires_auth(resource, op_config),
            has_schema=has_schema,
            response_schema=ctx.response_schema,
        )
        route.context["route_handlers"].append(handler)


class DeleteOperation:
    """DELETE /{pk} — delete a resource."""

    name = "delete"
    Options = EmptyOptions

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
        options: EmptyOptions,  # noqa: ARG002
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
            requires_auth=_op_requires_auth(resource, op_config),
        )
        route.context["route_handlers"].append(handler)


class ActionOperation:
    """POST /{pk}/{action_slug} — custom action endpoint.

    Each action is a separate operation entry in the config.
    The operation's name is the action slug; the ``fn`` option
    provides the dotted import path to the async callable.
    """

    name = "action"

    class Options(BaseModel):
        """Options for action operations."""

        fn: str
        params: list[FieldSpec] = []

    def contribute(
        self,
        specs: dict[str, FileSpecType],
        resource: ResourceConfig,
        ctx: SharedContext,
        op_config: OperationConfig,
        options: Options,
    ) -> None:
        """Emit action schema and route handler."""
        schema = specs["schema"]
        route = specs["route"]
        action_name = Name(op_config.name)

        # Schema contribution
        if options.params:
            field_dicts = _field_dicts(options.params)
            snippet = render_snippet(
                "fastapi/schema_parts/action_request.py.j2",
                request_class=action_name.suffixed("Request"),
                route_prefix=ctx.route_prefix,
                slug=action_name.slug,
                params=field_dicts,
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append(
                action_name.suffixed("Request"),
            )
            _add_field_type_imports(schema.imports, options.params)

        # ActionResponse (guard against duplicates)
        if "ActionResponse" not in schema.exports:
            snippet = render_snippet(
                "fastapi/schema_parts/action_response.py.j2",
            )
            schema.context["schema_classes"].append(snippet)
            schema.exports.append("ActionResponse")

        # Route contribution
        fn_module, fn_name = Name.from_dotted(options.fn)
        route.imports.add_from(fn_module, fn_name.raw)

        action_ctx = {
            "name": action_name.raw,
            "fn_name": fn_name.raw,
            "slug": action_name.slug,
            "handler_name": f"{action_name.raw}_action",
            "request_class": action_name.suffixed("Request"),
            "params": (_field_dicts(options.params) if options.params else []),
            "requires_auth": _op_requires_auth(resource, op_config),
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


def _make_schema_spec(model: Name, app: str, pkg: str) -> FileSpecType:
    """Create the schema FileSpec with base imports."""
    spec = FileSpecType(
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
) -> FileSpecType:
    """Create the serializer FileSpec with base imports.

    Resource fields are populated later by GetOperation or
    ListOperation when they have explicit fields.
    """
    spec = FileSpecType(
        path=f"{app}/serializers/{model.lower}.py",
        template="fastapi/serializer_outer.py.j2",
        imports=ImportCollector(),
        exports=[f"to_{model.lower}_resource"],
        package_prefix=pkg,
        context={
            "model_name": model.pascal,
            "model_lower": model.lower,
            "resource_fields": [],
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
) -> FileSpecType:
    """Create the route FileSpec with base imports."""
    utils_module = prefix_import(pkg, "utils")
    spec = FileSpecType(
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
        auth_module = prefix_import(pkg, "auth", "dependencies")
        spec.imports.add_from(auth_module, "get_current_user")

    session_module = prefix_import(pkg, ctx.session_module)
    spec.imports.add_from(session_module, ctx.get_db_fn)
    return spec
