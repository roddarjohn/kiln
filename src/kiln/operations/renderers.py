"""FastAPI renderers for build output types.

Each renderer is an ``@registry.renders(SomeOutput)`` generator
that yields fragments: a :class:`~foundry.render.FileFragment`
declaring the output file's wrapper template and scalar
context, plus one or more :class:`~foundry.render.SnippetFragment`
contributions into its slot lists.  The assembler groups
fragments by path, folds snippets into the file's context, and
renders each wrapper template once.

Per-op RouteHandler rendering is owned by each op module (e.g.
:mod:`kiln.operations.list`).  Those modules call
:func:`build_handler_fragment` with their op-specific body
template, context, and import tuple.  This module keeps only
the cross-cutting renderers (schema / enum / serializer /
testcase / static) plus a generic :class:`RouteHandler`
fallback for hand-written handlers that aren't one of the
registered subclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from foundry.env import render_template
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
from foundry.render import FileFragment, Fragment, SnippetFragment, registry
from kiln._helpers import PYTHON_TYPES

if TYPE_CHECKING:
    from collections.abc import Iterator

    import jinja2

    from foundry.render import RenderCtx
    from kiln.config.schema import ResourceConfig


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
    package_prefix: str
    route_prefix: str
    pk_name: str
    pk_py_type: str
    has_auth: bool
    session_module: str
    get_db_fn: str
    generate_tests: bool


def _resource_info(ctx: RenderCtx) -> _ResourceInfo:
    """Build a :class:`_ResourceInfo` from the renderer context.

    Walks ``ctx.store`` up from ``ctx.instance_id`` to the
    enclosing resource.  Every renderer that calls this handles
    an output produced at operation scope (below resource), so
    :meth:`BuildStore.ancestor_of` always finds the resource.
    """
    resource = cast(
        "ResourceConfig",
        ctx.store.ancestor_of(ctx.instance_id, "resource"),
    )
    config = ctx.config
    package_prefix = ctx.package_prefix

    model_dotted: str = getattr(resource, "model", "")
    model_module, model = Name.from_dotted(model_dotted)
    parts = model_module.rsplit(".", 1)
    app = parts[0] if len(parts) > 1 else model_module

    db = config.resolve_database(getattr(resource, "db_key", None))
    route_prefix = getattr(resource, "route_prefix", None)
    if not route_prefix:
        route_prefix = f"/{model.lower}s"

    return _ResourceInfo(
        model=model,
        model_module=model_module,
        app=app,
        package_prefix=package_prefix,
        route_prefix=route_prefix,
        pk_name=getattr(resource, "pk", "id"),
        pk_py_type=PYTHON_TYPES[getattr(resource, "pk_type", "uuid")],
        has_auth=getattr(config, "auth", None) is not None,
        session_module=db.session_module,
        get_db_fn=db.get_db_fn,
        generate_tests=getattr(resource, "generate_tests", False),
    )


# -------------------------------------------------------------------
# Built-in renderers -- register at module import time against the
# shared :data:`foundry.render.registry`.  Op-specific RouteHandler
# subclasses decorate their own module's renderer against the same
# registry; those registrations fire when the op module is imported
# (e.g. via entry points in the generate pipeline).
# -------------------------------------------------------------------


@registry.renders(SchemaClass)
def _schema_fragment(schema: SchemaClass, ctx: RenderCtx) -> Iterator[Fragment]:
    info = _resource_info(ctx)
    path = f"{info.app}/schemas/{info.model.lower}.py"
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("pydantic", "BaseModel")
    for f in schema.fields:
        py_type = f.py_type
        if py_type == "uuid.UUID":
            imports.add("uuid")
        elif py_type == "datetime":
            imports.add_from("datetime", "datetime")
        elif py_type == "date":
            imports.add_from("datetime", "date")
        elif py_type == "dict[str, Any]":
            imports.add_from("typing", "Any")

    yield FileFragment(
        path=path,
        template="fastapi/schema_outer.py.j2",
        context={"model_name": info.model.pascal},
    )
    yield SnippetFragment(
        path=path,
        slot="schema_classes",
        value=render_schema_class(schema, ctx.env),
        imports=imports,
    )


@registry.renders(EnumClass)
def _enum_fragment(enum: EnumClass, ctx: RenderCtx) -> Iterator[Fragment]:
    info = _resource_info(ctx)
    path = f"{info.app}/schemas/{info.model.lower}.py"
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("pydantic", "BaseModel")
    imports.add_from("enum", "Enum")
    yield FileFragment(
        path=path,
        template="fastapi/schema_outer.py.j2",
        context={"model_name": info.model.pascal},
    )
    yield SnippetFragment(
        path=path,
        slot="schema_classes",
        value=render_enum_class(enum),
        imports=imports,
    )


@registry.renders(RouteHandler)
def _handler_fragment(
    handler: RouteHandler, ctx: RenderCtx
) -> Iterator[Fragment]:
    """Render every route handler, op-specific or hand-written.

    Each op's ``build()`` stamps :attr:`RouteHandler.body_template`
    and :attr:`RouteHandler.body_context` on the handler so this
    single renderer covers every op.  Handlers with
    ``body_template=None`` fall back to the inline
    :attr:`~RouteHandler.body_lines` (rendered by
    :func:`_render_handler_string`).
    """
    info = _resource_info(ctx)
    path = f"{info.app}/routes/{info.model.lower}.py"

    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("typing", "Annotated")
    imports.add_from("fastapi", "APIRouter", "Depends")
    imports.add_from("sqlalchemy.ext.asyncio", "AsyncSession")
    imports.add_from(info.model_module, info.model.pascal)
    _add_pk_type_imports(imports, info.pk_py_type)

    session_mod = prefix_import(info.package_prefix, info.session_module)
    imports.add_from(session_mod, info.get_db_fn)

    if handler.status_code in (201, 204):
        imports.add_from("starlette", "status")

    schema_mod = prefix_import(
        info.package_prefix, info.app, "schemas", info.model.lower
    )
    if handler.request_schema:
        imports.add_from(schema_mod, handler.request_schema)

    response_schema = _response_schema_name(handler)
    if response_schema:
        imports.add_from(schema_mod, response_schema)
    if handler.serializer_fn:
        serializer_mod = prefix_import(
            info.package_prefix, info.app, "serializers", info.model.lower
        )
        imports.add_from(serializer_mod, handler.serializer_fn)

    for module, name in handler.extra_imports:
        imports.add_from(module, name)

    yield FileFragment(
        path=path,
        template="fastapi/route.py.j2",
        context={
            "model_name": info.model.pascal,
            "model_lower": info.model.lower,
            "route_prefix": info.route_prefix,
        },
    )
    if handler.body_template is None:
        yield SnippetFragment(
            path=path,
            slot="route_handlers",
            value=_render_handler_string(handler),
            imports=imports,
        )
    else:
        yield SnippetFragment(
            path=path,
            slot="route_handlers",
            template=handler.body_template,
            context={
                "model_name": info.model.pascal,
                "model_lower": info.model.lower,
                "pk_name": info.pk_name,
                "pk_py_type": info.pk_py_type,
                "get_db_fn": info.get_db_fn,
                "route_prefix": info.route_prefix,
                "extra_deps": handler.extra_deps,
                **handler.body_context,
            },
            imports=imports,
        )


@registry.renders(SerializerFn)
def _serializer_fragment(
    ser: SerializerFn, ctx: RenderCtx
) -> Iterator[Fragment]:
    info = _resource_info(ctx)
    ser_path = f"{info.app}/serializers/{info.model.lower}.py"
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from(info.model_module, info.model.pascal)
    schema_mod = prefix_import(
        info.package_prefix, info.app, "schemas", info.model.lower
    )
    imports.add_from(schema_mod, ser.schema_name)

    yield FileFragment(
        path=ser_path,
        template="fastapi/serializer_outer.py.j2",
        context={"model_name": info.model.pascal},
    )
    yield SnippetFragment(
        path=ser_path,
        slot="serializer_fns",
        value=render_serializer(ser, ctx.env),
        imports=imports,
    )

    if info.generate_tests:
        test_path = f"tests/test_{info.app}_{info.model.lower}.py"
        yield FileFragment(
            path=test_path,
            template="fastapi/test_outer.py.j2",
            context={"has_serializer_test": True},
        )
        for f in ser.fields:
            yield SnippetFragment(
                path=test_path,
                slot="serializer_fields",
                value={"name": f.name, "py_type": f.py_type},
            )


@registry.renders(TestCase)
def _testcase_fragment(tc: TestCase, ctx: RenderCtx) -> Iterator[Fragment]:
    info = _resource_info(ctx)
    if not info.generate_tests:
        return

    test_path = f"tests/test_{info.app}_{info.model.lower}.py"
    route_module = prefix_import(
        info.package_prefix, info.app, "routes", info.model.lower
    )

    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add("uuid")
    imports.add("pytest")
    imports.add("pytest_asyncio")
    imports.add_from("unittest.mock", "AsyncMock", "MagicMock")
    imports.add_from("httpx", "ASGITransport", "AsyncClient")
    imports.add_from("fastapi", "FastAPI")
    imports.add_from(route_module, "router")
    session_mod = prefix_import(info.package_prefix, info.session_module)
    imports.add_from(session_mod, info.get_db_fn)
    if info.has_auth:
        auth_module = prefix_import(info.package_prefix, "auth", "dependencies")
        imports.add_from(auth_module, "get_current_user")

    yield FileFragment(
        path=test_path,
        template="fastapi/test_outer.py.j2",
        context={
            "model_name": info.model.pascal,
            "model_lower": info.model.lower,
            "pk_name": info.pk_name,
            "pk_py_type": info.pk_py_type,
            "route_prefix": info.route_prefix,
            "has_auth": info.has_auth,
            "get_db_fn": info.get_db_fn,
            "route_module": route_module,
            "get_current_user_fn": (
                "get_current_user" if info.has_auth else None
            ),
        },
        imports=imports,
    )
    yield SnippetFragment(
        path=test_path,
        slot="test_cases",
        value={
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
        },
    )


@registry.renders(StaticFile)
def _static_fragment(sf: StaticFile, _ctx: RenderCtx) -> Iterator[Fragment]:
    yield FileFragment(
        path=sf.path,
        template=sf.template,
        context=dict(sf.context),
    )


# -------------------------------------------------------------------
# Shared helpers used by operation build() methods.
# -------------------------------------------------------------------


def utils_imports() -> list[tuple[str, str]]:
    """Return import pairs for the ``ingot`` runtime helpers.

    The three CRUD ops that load-or-404 a row (get, update,
    delete) all need ``get_object_from_query_or_404`` and
    ``assert_rowcount``; this centralizes the pair.
    """
    return [
        ("ingot", "get_object_from_query_or_404"),
        ("ingot", "assert_rowcount"),
    ]


# -------------------------------------------------------------------
# Content renderers -- produce code strings without touching paths or
# imports.  Exposed at module level so tests can exercise them
# directly and so renderers above can reuse them.
# -------------------------------------------------------------------


def render_schema_class(schema: SchemaClass, env: jinja2.Environment) -> str:
    """Render a Pydantic model class definition string."""
    fields = [
        {"name": f.name, "py_type": f.py_type, "optional": f.optional}
        for f in schema.fields
    ]
    return render_template(
        env=env,
        template_name="fastapi/schema_parts/schema_class.py.j2",
        class_name=schema.name,
        doc=schema.doc,
        fields=fields,
    ).strip()


def render_enum_class(enum: EnumClass) -> str:
    """Render an Enum class definition string."""
    lines = [f"class {enum.name}({enum.base}):"]
    for member_name, member_value in enum.members:
        lines.append(f"    {member_name} = {member_value!r}")
    return "\n".join(lines)


def render_serializer(ser: SerializerFn, env: jinja2.Environment) -> str:
    """Render a single serializer function as a standalone string."""
    fields = [{"name": f.name} for f in ser.fields]
    return render_template(
        env=env,
        template_name="fastapi/serializer_fn.py.j2",
        function_name=ser.function_name,
        model_name=ser.model_name,
        schema_name=ser.schema_name,
        fields=fields,
    ).strip()


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


def _response_schema_name(handler: RouteHandler) -> str | None:
    """Return the schema class referenced by the handler's response_model.

    Unwraps a single ``list[...]`` envelope so callers get the
    inner class name regardless of whether the handler returns
    one object or a list of objects.
    """
    rm = handler.response_model
    if not rm:
        return None
    if rm.startswith("list[") and rm.endswith("]"):
        return rm[len("list[") : -1]
    return rm


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
