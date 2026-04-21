"""FastAPI renderers for build output types.

Each renderer converts a build output object into a
:class:`~foundry.render.Fragment`: the target path, the shell
template that wraps the file, and the imports this contribution
needs.  The assembler groups fragments by path, unions imports,
and merges ``shell_context`` list values so multiple fragments
can stream into the same file.

Per-op RouteHandler rendering is owned by each op module (e.g.
:mod:`kiln.operations.list`).  Those modules call
:func:`build_handler_fragment` with their op-specific template
name, template context, and import tuple.  This module keeps
only the cross-cutting renderers (schema / enum / serializer /
testcase / static) plus a generic :class:`RouteHandler`
fallback for hand-written handlers that aren't one of the
registered subclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry.imports import ImportCollector
from foundry.naming import Name, prefix_import
from foundry.outputs import (
    EnumClass,
    Field,
    RouteHandler,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.render import Fragment, RenderRegistry
from kiln.generators._env import render_snippet
from kiln.generators._helpers import PYTHON_TYPES, resolve_db_session

if TYPE_CHECKING:
    from collections.abc import Sequence

    from foundry.render import RenderCtx

FASTAPI_TAGS = {"framework": "fastapi"}

# Module-level singleton registry.  Importing this module registers
# every built-in renderer below; importing an op module from
# :mod:`kiln.operations` registers that op's handler renderer.  This
# mirrors alembic's ``@Operations.register_operation`` pattern: a
# plugin self-registers on import, with no explicit ``register_*``
# call required.
FASTAPI_REGISTRY = RenderRegistry(active_tags=dict(FASTAPI_TAGS))


# -------------------------------------------------------------------
# Resource info -- carries the fields every per-resource renderer
# needs to compute paths, imports, and template context.
# -------------------------------------------------------------------


@dataclass(frozen=True)
class _ResourceInfo:
    """Derived-from-config state for one resource."""

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


def _resource_info(ctx: RenderCtx) -> _ResourceInfo:
    """Build a :class:`_ResourceInfo` from the renderer context.

    Expects ``ctx.extras["resource"]`` to be the
    :class:`~kiln.config.schema.ResourceConfig` for the current
    scope instance.  The assembler sets this when dispatching
    resource-scoped outputs.
    """
    resource = ctx.extras["resource"]
    config = ctx.config
    pkg = ctx.package_prefix

    model_dotted: str = getattr(resource, "model", "")
    model_module, model = Name.from_dotted(model_dotted)
    parts = model_module.rsplit(".", 1)
    app = parts[0] if len(parts) > 1 else model_module

    databases = getattr(config, "databases", [])
    db_key = getattr(resource, "db_key", None)
    session_module, get_db_fn = resolve_db_session(db_key, databases)
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
        generate_tests=getattr(resource, "generate_tests", False),
    )


# -------------------------------------------------------------------
# Built-in renderers -- register at module import time against the
# shared :data:`FASTAPI_REGISTRY`.  Op-specific RouteHandler
# subclasses decorate their own module's renderer against the same
# registry; those registrations fire when the op module is imported
# (e.g. via entry points in the generate pipeline).
# -------------------------------------------------------------------


@FASTAPI_REGISTRY.renders(SchemaClass, tags=FASTAPI_TAGS)
def _schema_fragment(schema: SchemaClass, ctx: RenderCtx) -> Fragment:
    info = _resource_info(ctx)
    rendered = render_schema_class(schema)
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("pydantic", "BaseModel")
    _add_field_imports(imports, schema.fields)
    return Fragment(
        path=f"{info.app}/schemas/{info.model.lower}.py",
        shell_template="fastapi/schema_outer.py.j2",
        shell_context={
            "model_name": info.model.pascal,
            "schema_classes": [rendered],
        },
        imports=imports,
    )


@FASTAPI_REGISTRY.renders(EnumClass, tags=FASTAPI_TAGS)
def _enum_fragment(enum: EnumClass, ctx: RenderCtx) -> Fragment:
    info = _resource_info(ctx)
    rendered = render_enum_class(enum)
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("pydantic", "BaseModel")
    imports.add_from("enum", "Enum")
    return Fragment(
        path=f"{info.app}/schemas/{info.model.lower}.py",
        shell_template="fastapi/schema_outer.py.j2",
        shell_context={
            "model_name": info.model.pascal,
            "schema_classes": [rendered],
        },
        imports=imports,
    )


@FASTAPI_REGISTRY.renders(RouteHandler, tags=FASTAPI_TAGS)
def _generic_handler_fragment(h: RouteHandler, ctx: RenderCtx) -> Fragment:
    """Fallback renderer for plain RouteHandler instances.

    Op-specific subclasses (:class:`GetRoute`, :class:`ListRoute`,
    ...) register their own renderers that build a body from an
    op template.  Handlers that reach this function have
    ``body_lines`` already populated and are rendered as-is.
    """
    return build_handler_fragment(h, ctx)


@FASTAPI_REGISTRY.renders(SerializerFn, tags=FASTAPI_TAGS)
def _serializer_fragment(ser: SerializerFn, ctx: RenderCtx) -> list[Fragment]:
    info = _resource_info(ctx)
    rendered = render_serializer(ser)
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from(info.model_module, info.model.pascal)
    schema_mod = prefix_import(info.pkg, info.app, "schemas", info.model.lower)
    imports.add_from(schema_mod, ser.schema_name)
    serializer_fragment = Fragment(
        path=f"{info.app}/serializers/{info.model.lower}.py",
        shell_template="fastapi/serializer_outer.py.j2",
        shell_context={
            "model_name": info.model.pascal,
            "serializer_fns": [rendered],
        },
        imports=imports,
    )
    if not info.generate_tests:
        return [serializer_fragment]
    test_aux = Fragment(
        path=f"tests/test_{info.app}_{info.model.lower}.py",
        shell_template="fastapi/test_outer.py.j2",
        shell_context={
            "has_serializer_test": True,
            "serializer_fields": [
                {"name": f.name, "py_type": f.py_type} for f in ser.fields
            ],
        },
    )
    return [serializer_fragment, test_aux]


@FASTAPI_REGISTRY.renders(TestCase, tags=FASTAPI_TAGS)
def _testcase_fragment(tc: TestCase, ctx: RenderCtx) -> list[Fragment]:
    info = _resource_info(ctx)
    if not info.generate_tests:
        return []
    imports = _test_file_imports(info)
    shell_context = _test_file_base_context(info)
    shell_context["test_cases"] = [_testcase_dict(tc)]
    return [
        Fragment(
            path=f"tests/test_{info.app}_{info.model.lower}.py",
            shell_template="fastapi/test_outer.py.j2",
            shell_context=shell_context,
            imports=imports,
        )
    ]


@FASTAPI_REGISTRY.renders(StaticFile, tags=FASTAPI_TAGS)
def _static_fragment(sf: StaticFile, _ctx: RenderCtx) -> Fragment:
    return Fragment(
        path=sf.path,
        shell_template=sf.template,
        shell_context=dict(sf.context),
    )


# -------------------------------------------------------------------
# Shared fragment builder -- called by per-op renderers.
# -------------------------------------------------------------------


def build_handler_fragment(  # noqa: PLR0913
    h: RouteHandler,
    ctx: RenderCtx,
    *,
    body_template: str | None = None,
    body_extra: dict[str, object] | None = None,
    sql_verb: str | None = None,
    needs_utils: bool = False,
) -> Fragment:
    """Build a :class:`Fragment` for one route handler.

    Args:
        h: The handler to render.
        ctx: Current render context.
        body_template: Path to the op-specific body template.
            ``None`` falls back to :func:`_render_handler_string`,
            which emits the handler's own ``body_lines`` verbatim.
        body_extra: Extra template variables merged on top of
            the shared handler context.
        sql_verb: SQLAlchemy verb (``"select"``, ``"insert"``,
            ``"update"``, ``"delete"``) the body needs, or
            ``None`` to skip the sqlalchemy import.
        needs_utils: Whether the body uses helpers from the
            generated ``utils`` module.

    Returns:
        A fragment targeting ``<app>/routes/<model>.py`` with
        merged imports and shell context.

    """
    info = _resource_info(ctx)
    if body_template is None:
        rendered = _render_handler_string(h)
    else:
        common: dict[str, object] = {
            "model_name": info.model.pascal,
            "model_lower": info.model.lower,
            "pk_name": info.pk_name,
            "pk_py_type": info.pk_py_type,
            "get_db_fn": info.get_db_fn,
            "route_prefix": info.route_prefix,
            "extra_deps": h.extra_deps,
        }
        rendered = ctx.env.get_template(body_template).render(
            **common, **(body_extra or {})
        )
    imports = _handler_imports(
        h, info, sql_verb=sql_verb, needs_utils=needs_utils
    )
    return Fragment(
        path=f"{info.app}/routes/{info.model.lower}.py",
        shell_template="fastapi/route.py.j2",
        shell_context={
            "model_name": info.model.pascal,
            "model_lower": info.model.lower,
            "route_prefix": info.route_prefix,
            "route_handlers": [rendered],
            "utils_module": prefix_import(info.pkg, "utils"),
        },
        imports=imports,
    )


# -------------------------------------------------------------------
# Content renderers -- produce code strings without touching paths or
# imports.  Exposed at module level so tests can exercise them
# directly and so Fragment builders above can reuse them.
# -------------------------------------------------------------------


def render_schema_class(schema: SchemaClass) -> str:
    """Render a Pydantic model class definition string."""
    fields = [
        {"name": f.name, "py_type": f.py_type, "optional": f.optional}
        for f in schema.fields
    ]
    return render_snippet(
        "fastapi/schema_parts/schema_class.py.j2",
        class_name=schema.name,
        doc=schema.doc,
        fields=fields,
    )


def render_enum_class(enum: EnumClass) -> str:
    """Render an Enum class definition string."""
    lines = [f"class {enum.name}({enum.base}):"]
    for member_name, member_value in enum.members:
        lines.append(f"    {member_name} = {member_value!r}")
    return "\n".join(lines)


def render_serializer(ser: SerializerFn) -> str:
    """Render a single serializer function as a standalone string."""
    fields = [{"name": f.name} for f in ser.fields]
    return render_snippet(
        "fastapi/serializer_fn.py.j2",
        function_name=ser.function_name,
        model_name=ser.model_name,
        schema_name=ser.schema_name,
        fields=fields,
    )


def render_router_mount(mount: object) -> str:
    """Render a router-mount snippet string.

    Retained as a standalone helper because ``RouterMount``
    outputs are currently consumed by the static-file router
    generator rather than the fragment merge loop.  Kept for
    test coverage of mount-line formatting.
    """
    module = getattr(mount, "module", "")
    alias = getattr(mount, "alias", "")
    prefix = getattr(mount, "prefix", None)
    prefix_arg = f', prefix="{prefix}"' if prefix else ""
    return (
        f"from {module} import router "
        f"as {alias}\n"
        f"app_router.include_router("
        f"{alias}{prefix_arg})"
    )


def _render_handler_string(handler: RouteHandler) -> str:
    """Render a RouteHandler into a standalone function string.

    Used for handlers whose :attr:`body_lines` are already
    populated (hand-written or unknown-op fallbacks).  Op-specific
    bodies are rendered from op templates via
    :func:`build_handler_fragment`.
    """
    lines: list[str] = list(handler.decorators)

    method = handler.method.lower()
    decorator_parts = [f'"{handler.path}"']
    if handler.response_model:
        decorator_parts.append(f"response_model={handler.response_model}")
    status_suffix = _status_suffix(handler.status_code)
    if status_suffix:
        decorator_parts.append(f"status_code=status.{status_suffix}")
    elif handler.status_code:
        decorator_parts.append(f"status_code={handler.status_code}")

    lines.append(f"@router.{method}({', '.join(decorator_parts)})")

    params = []
    for p in handler.params:
        if p.default is not None:
            params.append(f"    {p.name}: {p.annotation} = {p.default},")
        else:
            params.append(f"    {p.name}: {p.annotation},")

    return_type = handler.return_type or "object"
    lines.append(f"async def {handler.function_name}(")
    lines.extend(params)
    lines.append(f") -> {return_type}:")

    if handler.doc:
        lines.append(f'    """{handler.doc}"""')

    if handler.body_lines:
        lines.extend(f"    {line}" for line in handler.body_lines)
    else:
        lines.append("    pass")

    return "\n".join(lines)


def _status_suffix(code: int | None) -> str | None:
    """Map HTTP status codes to FastAPI constants.

    Args:
        code: HTTP status code.

    Returns:
        FastAPI status constant name, or ``None``.

    """
    mapping = {
        200: "HTTP_200_OK",
        201: "HTTP_201_CREATED",
        204: "HTTP_204_NO_CONTENT",
        400: "HTTP_400_BAD_REQUEST",
        404: "HTTP_404_NOT_FOUND",
        422: "HTTP_422_UNPROCESSABLE_ENTITY",
    }
    if code is None:
        return None
    return mapping.get(code)


def _response_schema_name(h: RouteHandler) -> str | None:
    """Return the schema class referenced by the handler's response_model.

    Unwraps a single ``list[...]`` envelope so callers get the
    inner class name regardless of whether the handler returns
    one object or a list of objects.
    """
    rm = h.response_model
    if not rm:
        return None
    if rm.startswith("list[") and rm.endswith("]"):
        return rm[len("list[") : -1]
    return rm


# -------------------------------------------------------------------
# Handler-level imports.  Union across all fragments produces the
# final route file's import block.
# -------------------------------------------------------------------


def _handler_imports(
    h: RouteHandler,
    info: _ResourceInfo,
    *,
    sql_verb: str | None = None,
    needs_utils: bool = False,
) -> ImportCollector:
    """Compute the imports contributed by a single route handler.

    Base imports (APIRouter, Annotated, session, model) are
    always added; per-handler imports are derived from the
    handler's fields (status code, response_model).  The caller
    supplies the sqlalchemy verb and whether the body uses the
    generated utils module -- both are op-specific and owned by
    each op's renderer.  Auth imports come from the auth op via
    ``extra_imports``.
    """
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("typing", "Annotated")
    imports.add_from("fastapi", "APIRouter", "Depends")
    imports.add_from("sqlalchemy.ext.asyncio", "AsyncSession")
    imports.add_from(info.model_module, info.model.pascal)
    _add_pk_type_imports(imports, info.pk_py_type)

    session_mod = prefix_import(info.pkg, info.session_module)
    imports.add_from(session_mod, info.get_db_fn)

    if h.status_code in (201, 204):
        imports.add_from("starlette", "status")

    if sql_verb:
        imports.add_from("sqlalchemy", sql_verb)
    if needs_utils:
        imports.add_from(
            prefix_import(info.pkg, "utils"),
            "get_object_from_query_or_404",
            "assert_rowcount",
        )

    if h.request_schema:
        schema_mod = prefix_import(
            info.pkg, info.app, "schemas", info.model.lower
        )
        imports.add_from(schema_mod, h.request_schema)

    response_schema = _response_schema_name(h)
    if response_schema:
        schema_mod = prefix_import(
            info.pkg, info.app, "schemas", info.model.lower
        )
        imports.add_from(schema_mod, response_schema)
    if h.serializer_fn:
        serializer_mod = prefix_import(
            info.pkg, info.app, "serializers", info.model.lower
        )
        imports.add_from(serializer_mod, h.serializer_fn)

    for module, name in h.extra_imports:
        imports.add_from(module, name)

    return imports


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


def _add_field_imports(
    imports: ImportCollector,
    fields: Sequence[Field],
) -> None:
    """Add type-specific imports for schema field types."""
    for f in fields:
        py_type = f.py_type
        if py_type == "uuid.UUID":
            imports.add("uuid")
        elif py_type == "datetime":
            imports.add_from("datetime", "datetime")
        elif py_type == "date":
            imports.add_from("datetime", "date")
        elif py_type == "dict[str, Any]":
            imports.add_from("typing", "Any")


# -------------------------------------------------------------------
# Test-file helpers
# -------------------------------------------------------------------


def _test_file_imports(info: _ResourceInfo) -> ImportCollector:
    """Return the base imports every generated test file needs."""
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add("uuid")
    imports.add("pytest")
    imports.add("pytest_asyncio")
    imports.add_from("unittest.mock", "AsyncMock", "MagicMock")
    imports.add_from("httpx", "ASGITransport", "AsyncClient")
    imports.add_from("fastapi", "FastAPI")
    route_module = prefix_import(info.pkg, info.app, "routes", info.model.lower)
    imports.add_from(route_module, "router")
    session_mod = prefix_import(info.pkg, info.session_module)
    imports.add_from(session_mod, info.get_db_fn)
    if info.has_auth:
        auth_module = prefix_import(info.pkg, "auth", "dependencies")
        imports.add_from(auth_module, "get_current_user")
    return imports


def _test_file_base_context(info: _ResourceInfo) -> dict[str, object]:
    """Build the per-file context for the test outer template.

    ``has_serializer_test`` and ``serializer_fields`` are owned
    by the :class:`SerializerFn` fragment so they are absent
    here.  The template treats both as optional/falsy by default.
    """
    route_module = prefix_import(info.pkg, info.app, "routes", info.model.lower)
    get_current_user_fn = "get_current_user" if info.has_auth else None
    return {
        "model_name": info.model.pascal,
        "model_lower": info.model.lower,
        "pk_name": info.pk_name,
        "pk_py_type": info.pk_py_type,
        "route_prefix": info.route_prefix,
        "has_auth": info.has_auth,
        "get_db_fn": info.get_db_fn,
        "route_module": route_module,
        "get_current_user_fn": get_current_user_fn,
    }


def _testcase_dict(tc: TestCase) -> dict[str, object]:
    """Convert a :class:`TestCase` into the dict shape the template expects."""
    return {
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
