"""Assembler: build store to GeneratedFile list.

Groups build outputs by target output file, runs renderers,
assembles imports, and produces a flat list of
:class:`~foundry.spec.GeneratedFile` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from foundry.imports import ImportCollector
from foundry.naming import Name, prefix_import
from foundry.outputs import (
    EnumClass,
    RouteHandler,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.spec import FileSpec, GeneratedFile, wire_exports
from kiln.generators._helpers import PYTHON_TYPES, resolve_db_session

if TYPE_CHECKING:
    from collections.abc import Sequence

    from foundry.render import BuildStore, RenderCtx, RenderRegistry


def assemble(
    store: BuildStore,
    registry: RenderRegistry,
    ctx: RenderCtx,
) -> list[GeneratedFile]:
    """Turn a build store into rendered output files.

    This is the bridge between the engine's build phase and
    the final file output.  It groups build outputs by type,
    renders each with the appropriate renderer, and assembles
    them into complete files.

    Args:
        store: The build store from the engine's build phase.
        registry: Render registry with registered renderers.
        ctx: Render context (env, config, prefix).

    Returns:
        List of :class:`GeneratedFile` objects ready for
        output.

    """
    files: list[GeneratedFile] = []

    # Static files render directly — one output = one file.
    for obj in store.get_by_type(StaticFile):
        content = registry.render(obj, ctx)
        if isinstance(obj, StaticFile):
            files.append(GeneratedFile(path=obj.path, content=content))

    # Per-resource assembly.
    files.extend(_assemble_resources(store, registry, ctx))

    return files


@dataclass
class _ResourceInfo:
    """Bundled context for assembling one resource's files."""

    model: Name
    model_module: str
    app: str
    pkg: str
    route_prefix: str
    pk_name: str
    pk_py_type: str
    has_auth: bool
    session_module: str
    get_db_fn: str
    generate_tests: bool


def _resource_info(
    resource: object,
    config: object,
    pkg: str,
) -> _ResourceInfo:
    """Extract assembly context from a resource config.

    Args:
        resource: The resource config object.
        config: The project config.
        pkg: Package prefix string.

    Returns:
        Populated :class:`_ResourceInfo`.

    """
    model_dotted: str = getattr(resource, "model", "")
    model_module, model = Name.from_dotted(model_dotted)
    parts = model_module.rsplit(".", 1)
    app = parts[0] if len(parts) > 1 else model_module

    databases = getattr(config, "databases", [])
    db_key = getattr(resource, "db_key", None)
    session_module, get_db_fn = resolve_db_session(
        db_key,
        databases,
    )
    route_prefix = getattr(resource, "route_prefix", None)
    if not route_prefix:
        route_prefix = f"/{model.lower}s"

    return _ResourceInfo(
        model=model,
        model_module=model_module,
        app=app,
        pkg=pkg,
        route_prefix=route_prefix,
        pk_name=getattr(resource, "pk", "id"),
        pk_py_type=PYTHON_TYPES[getattr(resource, "pk_type", "uuid")],
        has_auth=getattr(config, "auth", None) is not None,
        session_module=session_module,
        get_db_fn=get_db_fn,
        generate_tests=getattr(
            resource,
            "generate_tests",
            False,
        ),
    )


def _assemble_resources(
    store: BuildStore,
    registry: RenderRegistry,
    ctx: RenderCtx,
) -> list[GeneratedFile]:
    """Assemble per-resource files from the build store.

    Args:
        store: Build store with all outputs.
        registry: Render registry.
        ctx: Render context.

    Returns:
        Generated files for all resources.

    """
    config = ctx.config
    pkg = ctx.package_prefix
    files: list[GeneratedFile] = []

    for resource in _get_resources(config):
        _, model = Name.from_dotted(getattr(resource, "model", ""))
        instance_id = model.lower

        all_items = store.get_by_scope("resource", instance_id)
        if not all_items:
            continue

        info = _resource_info(resource, config, pkg)
        specs = _build_resource_specs(
            info,
            all_items,
            registry,
            ctx,
        )

        wire_exports(specs)
        files.extend(spec.render(ctx.env) for spec in specs.values())

    return files


def _build_resource_specs(
    info: _ResourceInfo,
    all_items: list[object],
    registry: RenderRegistry,
    ctx: RenderCtx,
) -> dict[str, FileSpec]:
    """Create and populate file specs for one resource.

    Args:
        info: Bundled resource context.
        all_items: All build outputs for this resource.
        registry: Render registry.
        ctx: Render context.

    Returns:
        Dict of file specs keyed by role (schema, route, etc).

    """
    schemas = [o for o in all_items if isinstance(o, SchemaClass)]
    enums = [o for o in all_items if isinstance(o, EnumClass)]
    handlers = [o for o in all_items if isinstance(o, RouteHandler)]
    serializers = [o for o in all_items if isinstance(o, SerializerFn)]
    test_cases = [o for o in all_items if isinstance(o, TestCase)]

    specs: dict[str, FileSpec] = {}

    if schemas or enums:
        specs["schema"] = _make_schema_spec(
            info,
            schemas,
            enums,
            registry,
            ctx,
        )

    if serializers:
        specs["serializer"] = _make_serializer_spec(
            info,
            serializers,
            registry,
            ctx,
        )

    if handlers:
        specs["route"] = _make_route_spec(
            info,
            handlers,
            ctx,
        )

    if info.generate_tests and test_cases:
        specs["test"] = _make_test_spec(
            info,
            test_cases,
            serializers,
        )

    return specs


def _make_schema_spec(
    info: _ResourceInfo,
    schemas: list[SchemaClass],
    enums: list[EnumClass],
    registry: RenderRegistry,
    ctx: RenderCtx,
) -> FileSpec:
    """Create the schema file spec.

    Args:
        info: Bundled resource context.
        schemas: Schema class outputs.
        enums: Enum class outputs.
        registry: Render registry.
        ctx: Render context.

    Returns:
        Populated :class:`FileSpec`.

    """
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("pydantic", "BaseModel")

    rendered: list[str] = []
    exports: list[str] = []
    for enum in enums:
        rendered.append(registry.render(enum, ctx))
        exports.append(enum.name)
    for schema in schemas:
        rendered.append(registry.render(schema, ctx))
        exports.append(schema.name)
        _add_field_imports(imports, schema.fields)

    return FileSpec(
        path=f"{info.app}/schemas/{info.model.lower}.py",
        template="fastapi/schema_outer.py.j2",
        imports=imports,
        exports=exports,
        package_prefix=info.pkg,
        context={
            "model_name": info.model.pascal,
            "schema_classes": rendered,
        },
    )


def _make_serializer_spec(
    info: _ResourceInfo,
    serializers: list[SerializerFn],
    registry: RenderRegistry,
    ctx: RenderCtx,
) -> FileSpec:
    """Create the serializer file spec.

    Args:
        info: Bundled resource context.
        serializers: Serializer function outputs.
        registry: Render registry.
        ctx: Render context.

    Returns:
        Populated :class:`FileSpec`.

    """
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from(info.model_module, info.model.pascal)

    exports = [s.function_name for s in serializers]

    # Serializer rendering uses the template directly
    rendered = registry.render(serializers[0], ctx)

    return FileSpec(
        path=(f"{info.app}/serializers/{info.model.lower}.py"),
        template="fastapi/serializer_outer.py.j2",
        imports=imports,
        exports=exports,
        package_prefix=info.pkg,
        context={
            "model_name": info.model.pascal,
            "model_lower": info.model.lower,
            "resource_class": serializers[0].schema_name,
            "resource_fields": [
                {"name": f.name, "py_type": f.py_type}
                for f in serializers[0].fields
            ],
            "rendered_serializer": rendered,
        },
    )


def _make_route_spec(
    info: _ResourceInfo,
    handlers: list[RouteHandler],
    ctx: RenderCtx,
) -> FileSpec:
    """Create the route file spec.

    Args:
        info: Bundled resource context.
        handlers: Route handler outputs.
        ctx: Render context.

    Returns:
        Populated :class:`FileSpec`.

    """
    rendered = [_render_handler_body(h, info, ctx) for h in handlers]
    imports = _route_imports(info, handlers)

    return FileSpec(
        path=f"{info.app}/routes/{info.model.lower}.py",
        template="fastapi/route.py.j2",
        imports=imports,
        exports=["router"],
        package_prefix=info.pkg,
        context={
            "model_name": info.model.pascal,
            "model_lower": info.model.lower,
            "route_prefix": info.route_prefix,
            "route_handlers": rendered,
            "utils_module": prefix_import(
                info.pkg,
                "utils",
            ),
        },
    )


@dataclass
class _HandlerImportSummary:
    """Aggregate import needs computed from a list of handlers."""

    needs_status: bool = False
    needs_utils: bool = False
    sqlalchemy_verbs: set[str] = field(default_factory=set)


def _scan_handler_imports(
    imports: ImportCollector,
    info: _ResourceInfo,
    handlers: list[RouteHandler],
) -> _HandlerImportSummary:
    """Walk handlers; add schema/serializer imports; return summary."""
    summary = _HandlerImportSummary()
    schema_mod = prefix_import(
        info.pkg,
        info.app,
        "schemas",
        info.model.lower,
    )
    serializer_mod = prefix_import(
        info.pkg,
        info.app,
        "serializers",
        info.model.lower,
    )
    for h in handlers:
        if h.status_code in (201, 204):
            summary.needs_status = True
        verb, needs_util = _handler_deps(h)
        if verb:
            summary.sqlalchemy_verbs.add(verb)
        if needs_util:
            summary.needs_utils = True
        if h.request_schema:
            imports.add_from(schema_mod, h.request_schema)
        if h.response_model and "Resource" in h.response_model:
            imports.add_from(schema_mod, info.model.suffixed("Resource"))
            imports.add_from(
                serializer_mod,
                f"to_{info.model.lower}_resource",
            )
        for module, name in h.extra_imports:
            imports.add_from(module, name)
    return summary


def _route_imports(
    info: _ResourceInfo,
    handlers: list[RouteHandler],
) -> ImportCollector:
    """Compute imports for a resource's route file from its handlers.

    Base imports come from the operation/scaffolding; extensions
    contribute via ``handler.extra_imports``.
    """
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("typing", "Annotated")
    imports.add_from("fastapi", "APIRouter", "Depends")
    imports.add_from("sqlalchemy.ext.asyncio", "AsyncSession")

    session_mod = prefix_import(info.pkg, info.session_module)
    imports.add_from(session_mod, info.get_db_fn)

    summary = _scan_handler_imports(imports, info, handlers)

    if summary.needs_status:
        imports.add_from("starlette", "status")
    if summary.sqlalchemy_verbs:
        imports.add_from("sqlalchemy", *sorted(summary.sqlalchemy_verbs))
    if summary.needs_utils:
        imports.add_from(
            prefix_import(info.pkg, "utils"),
            "get_object_from_query_or_404",
            "assert_rowcount",
        )
    imports.add_from(info.model_module, info.model.pascal)
    _add_pk_type_imports(imports, info.pk_py_type)
    return imports


def _handler_deps(h: RouteHandler) -> tuple[str | None, bool]:
    """Return ``(sqlalchemy_verb, needs_utils)`` for a handler."""
    mapping = {
        "get": ("select", True),
        "list": ("select", False),
        "create": ("insert", False),
        "update": ("update", True),
        "delete": ("delete", True),
    }
    return mapping.get(h.op_name, (None, False))


def _render_handler_body(
    h: RouteHandler,
    info: _ResourceInfo,
    ctx: RenderCtx,
) -> str:
    """Render a handler using its op-specific template."""
    env = ctx.env
    common = {
        "model_name": info.model.pascal,
        "model_lower": info.model.lower,
        "pk_name": info.pk_name,
        "pk_py_type": info.pk_py_type,
        "get_db_fn": info.get_db_fn,
        "route_prefix": info.route_prefix,
        "extra_deps": h.extra_deps,
    }
    if h.op_name == "get":
        tmpl = env.get_template("fastapi/ops/get.py.j2")
        return tmpl.render(
            **common,
            has_resource_schema=bool(h.response_model),
        )
    if h.op_name == "list":
        tmpl = env.get_template("fastapi/ops/list.py.j2")
        return tmpl.render(
            **common,
            http_method="get",
            route_path="/",
            response_model=h.response_model or "list",
            return_type=h.return_type or "object",
            has_resource_schema=bool(
                h.response_model and "Resource" in h.response_model,
            ),
            extra_params=[],
            query_modifiers=[],
            result_expression=None,
        )
    if h.op_name == "create":
        tmpl = env.get_template("fastapi/ops/create.py.j2")
        return tmpl.render(
            **common,
            response_schema=h.response_model,
            has_schema=bool(h.request_schema),
        )
    if h.op_name == "update":
        tmpl = env.get_template("fastapi/ops/update.py.j2")
        return tmpl.render(
            **common,
            response_schema=h.response_model,
            has_schema=bool(h.request_schema),
        )
    if h.op_name == "delete":
        tmpl = env.get_template("fastapi/ops/delete.py.j2")
        return tmpl.render(**common)
    # Fallback: stub handler
    return f"# unsupported op_name: {h.op_name}\n"


def _add_pk_type_imports(
    imports: ImportCollector,
    pk_py_type: str,
) -> None:
    """Add imports for PK primitive types (uuid, datetime, etc)."""
    if "uuid" in pk_py_type:
        imports.add("uuid")
    if "datetime" in pk_py_type:
        imports.add_from("datetime", "datetime")
    if "date" in pk_py_type and "datetime" not in pk_py_type:
        imports.add_from("datetime", "date")


def _make_test_spec(
    info: _ResourceInfo,
    test_cases: list[TestCase],
    serializers: list[SerializerFn],
) -> FileSpec:
    """Create the test file spec.

    Args:
        info: Bundled resource context.
        test_cases: Test case outputs.
        serializers: Serializer outputs (for field tests).

    Returns:
        Populated :class:`FileSpec`.

    """
    route_module = prefix_import(
        info.pkg,
        info.app,
        "routes",
        info.model.lower,
    )

    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add("uuid")
    imports.add("pytest")
    imports.add("pytest_asyncio")
    imports.add_from(
        "unittest.mock",
        "AsyncMock",
        "MagicMock",
    )
    imports.add_from(
        "httpx",
        "ASGITransport",
        "AsyncClient",
    )
    imports.add_from("fastapi", "FastAPI")
    imports.add_from(route_module, "router")

    session_mod = prefix_import(
        info.pkg,
        info.session_module,
    )
    imports.add_from(session_mod, info.get_db_fn)

    has_serializer_test = bool(serializers)
    serializer_fields: list[dict[str, str]] = []
    if serializers:
        serializer_fields = [
            {"name": f.name, "py_type": f.py_type}
            for f in serializers[0].fields
        ]

    tc_dicts = [
        {
            "op_name": tc.op_name,
            "method": tc.method,
            "path": tc.path,
            "status_success": tc.status_success,
            "status_not_found": tc.status_not_found,
            "status_invalid": tc.status_invalid,
            "requires_auth": tc.requires_auth,
            "has_request_body": tc.has_request_body,
            "request_schema": tc.request_schema,
            "request_fields": tc.request_fields,
            "action_name": tc.action_name,
        }
        for tc in test_cases
    ]

    get_current_user_fn = None
    if info.has_auth:
        auth_module = prefix_import(
            info.pkg,
            "auth",
            "dependencies",
        )
        imports.add_from(auth_module, "get_current_user")
        get_current_user_fn = "get_current_user"

    return FileSpec(
        path=(f"tests/test_{info.app}_{info.model.lower}.py"),
        template="fastapi/test_outer.py.j2",
        imports=imports,
        package_prefix=info.pkg,
        context={
            "model_name": info.model.pascal,
            "model_lower": info.model.lower,
            "pk_name": info.pk_name,
            "pk_py_type": info.pk_py_type,
            "route_prefix": info.route_prefix,
            "has_auth": info.has_auth,
            "get_db_fn": info.get_db_fn,
            "route_module": route_module,
            "test_cases": tc_dicts,
            "has_serializer_test": has_serializer_test,
            "serializer_fields": serializer_fields,
            "get_current_user_fn": get_current_user_fn,
        },
    )


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _get_resources(config: object) -> list[object]:
    """Extract resource configs from a project config.

    Handles both single-app and multi-app configs.

    Args:
        config: The project config model.

    Returns:
        List of resource config objects.

    """
    resources = getattr(config, "resources", [])
    if resources:
        return list(resources)

    # Multi-app: collect from each app
    apps = getattr(config, "apps", [])
    result: list[object] = []
    for app_ref in apps:
        app_cfg = getattr(app_ref, "config", None)
        if app_cfg:
            result.extend(getattr(app_cfg, "resources", []))
    return result


def _add_field_imports(
    imports: ImportCollector,
    fields: Sequence[object],
) -> None:
    """Add type-specific imports for field types.

    Args:
        imports: Import collector to add to.
        fields: List of Field objects.

    """
    for f in fields:
        py_type = getattr(f, "py_type", "")
        if py_type == "uuid.UUID":
            imports.add("uuid")
        elif py_type == "datetime":
            imports.add_from("datetime", "datetime")
        elif py_type == "date":
            imports.add_from("datetime", "date")
        elif py_type == "dict[str, Any]":
            imports.add_from("typing", "Any")
