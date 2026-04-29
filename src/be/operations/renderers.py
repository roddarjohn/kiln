"""FastAPI renderers for build output types.

Each renderer is an ``@registry.renders(SomeOutput)`` generator
that yields fragments: a :class:`~foundry.render.FileFragment`
declaring the output file's wrapper template and scalar
context, plus one or more :class:`~foundry.render.SnippetFragment`
contributions into its slot lists.  The assembler groups
fragments by path, folds snippets into the file's context, and
renders each wrapper template once.

Per-op RouteHandler rendering is owned by each op module (e.g.
:mod:`be.operations.list`).  Those modules call
``build_handler_fragment`` with their op-specific body template,
context, and import tuple.  This module keeps only the
cross-cutting renderers (schema / enum / serializer / testcase /
static) plus a generic
:class:`~be.operations.types.RouteHandler` fallback for
hand-written handlers that aren't one of the registered
subclasses.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from be.config.schema import PYTHON_TYPES
from be.operations._naming import (
    app_module_for,
    collection_specs_const,
    object_specs_const,
)
from be.operations.list import ListResult
from be.operations.types import (
    EnumClass,
    Field,
    RouteHandler,
    SchemaClass,
    SerializerFn,
    TestCase,
)
from foundry.imports import ImportCollector
from foundry.naming import Name, prefix_import
from foundry.render import FileFragment, Fragment, SnippetFragment, registry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from be.config.schema import OperationConfig, ResourceConfig
    from foundry.render import RenderCtx


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
    session_schema: str | None
    """Dotted path to the consumer's session Pydantic model
    (e.g. ``"myapp.auth.Session"``), or ``None`` when auth is not
    configured.  Used by the action-framework path to type the
    serializer's ``session`` parameter."""
    session_module: str
    get_db_fn: str
    generate_tests: bool
    include_actions_in_dump: bool


def _resource_info(ctx: RenderCtx) -> _ResourceInfo:
    """Build a :class:`_ResourceInfo` from the renderer context.

    Walks ``ctx.store`` up from ``ctx.instance_id`` to the
    enclosing resource.  Most callers handle an output produced
    at operation scope (below resource), so :meth:`ancestor_of`
    finds the resource.  Resource-scope ops (Permissions) yield
    outputs stored at the resource id itself; the fallback uses
    :meth:`BuildStore.instance_at` to recover the resource when
    ``instance_id`` *is* the resource.
    """
    if ctx.store.scope_of(ctx.instance_id).name == "resource":
        resource = cast(
            "ResourceConfig",
            ctx.store.instance_at(ctx.instance_id),
        )

    else:
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )

    config = ctx.config
    package_prefix = ctx.package_prefix

    model_dotted: str = getattr(resource, "model", "")
    model_module, model = Name.from_dotted(model_dotted)
    app = app_module_for(model_dotted)

    db = config.resolve_database(getattr(resource, "db_key", None))
    route_prefix = getattr(resource, "route_prefix", None)

    if not route_prefix:
        route_prefix = f"/{model.lower}s"

    auth = getattr(config, "auth", None)
    session_schema = getattr(auth, "session_schema", None) if auth else None

    return _ResourceInfo(
        model=model,
        model_module=model_module,
        app=app,
        package_prefix=package_prefix,
        route_prefix=route_prefix,
        pk_name=getattr(resource, "pk", "id"),
        pk_py_type=PYTHON_TYPES[resource.pk_type],
        has_auth=auth is not None,
        session_schema=session_schema,
        session_module=db.session_module,
        get_db_fn=db.get_db_fn,
        generate_tests=getattr(resource, "generate_tests", False),
        include_actions_in_dump=getattr(
            resource, "include_actions_in_dump", False
        ),
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
    """Render a SchemaClass into the schemas file.

    Dispatches on :attr:`SchemaClass.body_template`:

    - When set: uses that template with ``body_context`` +
      ``extra_imports``.  ``fields`` / ``validators`` are ignored.
    - When unset: renders ``fields`` through the default
      ``schema_class.py.j2`` template, auto-collecting imports
      for common field types (uuid, datetime, date, Any).
    """
    info = _resource_info(ctx)
    path = f"{info.app}/schemas/{info.model.lower}.py"

    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from("pydantic", "BaseModel")

    yield FileFragment(
        path=path,
        template="fastapi/schema_outer.py.j2",
        context={"model_name": info.model.pascal},
    )

    if schema.body_template is not None:
        for module, name in schema.extra_imports:
            imports.add_from(module, name)

        yield SnippetFragment(
            path=path,
            slot="schema_classes",
            template=schema.body_template,
            context=schema.body_context,
            imports=imports,
        )
        return

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

        elif py_type == "list[ActionRef]":
            imports.add_from("ingot.actions", "ActionRef")

    yield SnippetFragment(
        path=path,
        slot="schema_classes",
        template="fastapi/schema_parts/schema_class.py.j2",
        context={
            "class_name": schema.name,
            "doc": schema.doc,
            "fields": [
                {
                    "name": f.name,
                    "py_type": f.py_type,
                    "optional": f.optional,
                }
                for f in schema.fields
            ],
        },
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

    if _status_suffix(handler.status_code) is not None:
        imports.add_from("starlette", "status")

    schema_mod = prefix_import(
        info.package_prefix, info.app, "schemas", info.model.lower
    )

    if handler.request_schema:
        request_mod = handler.request_schema_module or schema_mod
        imports.add_from(request_mod, handler.request_schema)

    response_schema = _response_schema_name(handler)

    if response_schema:
        response_mod = handler.response_schema_module or schema_mod
        imports.add_from(response_mod, response_schema)

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
    yield SnippetFragment(
        path=path,
        slot="route_handlers",
        template=handler.body_template or "fastapi/handler_default.py.j2",
        context=_handler_context(handler=handler, info=info),
        imports=imports,
    )


def _handler_context(
    handler: RouteHandler,
    info: _ResourceInfo,
) -> dict[str, object]:
    """Build the unified render context used by every handler template.

    The default handler template defines the wrapper (decorator,
    signature, docstring, body block).  Op-specific templates
    ``{% extends %}`` the default and override the ``body`` block
    only, so they share the same context shape.

    ``handler.params`` is deduplicated by name (first occurrence
    wins) so after-children ops can append parameters without
    checking whether they're already present -- the same idiom
    :class:`~foundry.imports.ImportCollector` uses for imports.
    """
    seen: set[str] = set()
    params: list[dict[str, object]] = []

    for p in handler.params:
        if p.name in seen:
            continue

        seen.add(p.name)
        params.append(
            {"name": p.name, "annotation": p.annotation, "default": p.default}
        )

    params.append(
        {
            "name": "db",
            "annotation": (
                f"Annotated[AsyncSession, Depends({info.get_db_fn})]"
            ),
            "default": None,
        }
    )

    return {
        "decorators": handler.decorators,
        "method": handler.method.lower(),
        "path": handler.path,
        "response_model": handler.response_model,
        "status_suffix": _status_suffix(handler.status_code),
        "status_code": handler.status_code,
        "function_name": handler.function_name,
        "params": params,
        "extra_deps": handler.extra_deps,
        "return_type": handler.return_type or "None",
        "doc": handler.doc,
        "body_lines": handler.body_lines,
        "serializer_fn": handler.serializer_fn,
        "request_schema": handler.request_schema,
        # Resource-derived context every op body may reference.
        "model_name": info.model.pascal,
        "model_lower": info.model.lower,
        "pk_name": info.pk_name,
        "pk_py_type": info.pk_py_type,
        "get_db_fn": info.get_db_fn,
        "route_prefix": info.route_prefix,
        # Op-specific extras (e.g. query_modifiers, result_expression).
        **handler.body_context,
    }


def _mock_row_lines(fields: list[Field], target: str) -> list[str]:
    """Emit ``_mock_row()`` assignment lines for ``fields`` under ``target``.

    Scalars become ``{target}.{name} = _sample("{py_type}")``.

    Scalar-nested fields descend via dotted paths
    (``row.author.id = _sample(...)``) — ``MagicMock`` auto-creates
    the intermediate attribute.

    Collection-nested fields (``many=True``) are intentionally left
    unset: ``MagicMock`` supports ``__iter__`` returning an empty
    iterator by default, so the generated list comprehension
    ``[to_sub(x) for x in obj.tags]`` produces ``[]`` without any
    explicit fixture plumbing.  Pydantic accepts an empty
    ``list[Nested]``, so the route test still passes.
    """
    lines: list[str] = []

    for f in fields:
        if f.nested_serializer is not None and f.many:
            continue

        if f.nested_serializer is not None:
            lines.extend(
                _mock_row_lines(f.nested_fields or [], f"{target}.{f.name}")
            )
            continue

        lines.append(f'{target}.{f.name} = _sample("{f.py_type}")')

    return lines


@registry.renders(SerializerFn)
def _serializer_fragment(
    ser: SerializerFn, ctx: RenderCtx
) -> Iterator[Fragment]:
    info = _resource_info(ctx)
    ser_path = f"{info.app}/serializers/{info.model.lower}.py"
    imports = ImportCollector()
    imports.add_from("__future__", "annotations")
    imports.add_from(ser.model_module, ser.model_name)
    schema_mod = prefix_import(
        info.package_prefix, info.app, "schemas", info.model.lower
    )
    imports.add_from(schema_mod, ser.schema_name)

    actions_context: dict[str, object] = {}

    if ser.include_actions:
        actions_context = _wire_action_dump(info, imports)
        # Skip the synthetic ``actions`` field in the per-field
        # render loop -- the template's actions branch builds it
        # directly via ``available_actions(...)``.
        rendered_fields = [
            f for f in ser.fields if f.py_type != "list[ActionRef]"
        ]

    else:
        rendered_fields = ser.fields

    yield FileFragment(
        path=ser_path,
        template="fastapi/serializer_outer.py.j2",
        context={"model_name": info.model.pascal},
    )
    yield SnippetFragment(
        path=ser_path,
        slot="serializer_fns",
        template="fastapi/serializer_fn.py.j2",
        context={
            "function_name": ser.function_name,
            "model_name": ser.model_name,
            "schema_name": ser.schema_name,
            "fields": [
                {
                    "name": f.name,
                    "nested_serializer": f.nested_serializer,
                    "many": f.many,
                }
                for f in rendered_fields
            ],
            "include_actions": ser.include_actions,
            **actions_context,
        },
        imports=imports,
    )

    # Only the resource serializer contributes to the test file; the
    # test template renders exactly one `test_to_{model}_resource_*`
    # function, so emitting from list_item too would duplicate the
    # mock-row assignments and assertions.
    is_resource_ser = ser.function_name == f"to_{info.model.lower}_resource"

    if info.generate_tests and is_resource_ser:
        test_path = f"tests/test_{info.app}_{info.model.lower}.py"
        ser_mod = prefix_import(
            info.package_prefix, info.app, "serializers", info.model.lower
        )
        test_imports = ImportCollector()
        test_imports.add_from(ser_mod, ser.function_name)
        yield FileFragment(
            path=test_path,
            template="fastapi/test_outer.py.j2",
            context={
                "has_serializer_test": True,
                "mock_row_lines": _mock_row_lines(ser.fields, "row"),
            },
            imports=test_imports,
        )

        for f in ser.fields:
            # Nested fields don't roundtrip through the naive mock-row
            # serializer test (the test compares ORM attrs to schema
            # attrs directly; nested fields go through a sub-serializer
            # and produce a different object).
            if f.nested_serializer is not None:
                continue

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
        deps_module = prefix_import(info.package_prefix, "auth", "dependencies")
        imports.add_from(deps_module, "get_session")

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
            "get_session_fn": ("get_session" if info.has_auth else None),
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


@registry.renders(ListResult)
def _list_result_fragment(
    _result: ListResult, _ctx: RenderCtx
) -> Iterator[Fragment]:
    """ListResult is an internal bundle for modifier ops; emit nothing.

    The individual outputs it references (ListItem / SearchRequest /
    handler / etc.) are yielded separately by the list op and
    rendered through their own registered renderers.
    """
    return iter(())


# -------------------------------------------------------------------
# Shared helpers used by operation build() methods.
# -------------------------------------------------------------------


def _wire_action_dump(
    info: _ResourceInfo,
    imports: ImportCollector,
) -> dict[str, object]:
    """Register imports and return template context for an action dump.

    The action-framework path turns the resource's top-level
    serializer ``async``, threads ``session`` through, and folds
    ``available_actions`` against the per-resource registry tuples
    into the schema construction.  Both the runtime helper
    (:func:`ingot.actions.available_actions`) and the generated
    registry constants come from this single wiring point so the
    serializer renderer stays free of action-specific knowledge.

    Args:
        info: Cached resource info for the current render.
        imports: Collector to mutate; new entries land in the
            serializer file's import block.

    Returns:
        Template context keys consumed by ``serializer_fn.py.j2``
        on the action-dump branch:
        ``object_specs_const`` and ``collection_specs_const``
        (the registry constant names) plus ``session_type`` (the
        bare class name used in the function signature).

    Raises:
        ValueError: If auth is not configured -- the action
            framework relies on a session, so a resource that
            opts in must also have a session schema.

    """
    if info.session_schema is None:
        msg = (
            "include_actions_in_dump=True requires auth to be "
            "configured -- the serializer takes a session "
            "parameter to evaluate guards"
        )
        raise ValueError(msg)

    session_module, session_name = info.session_schema.rsplit(".", 1)
    actions_module = prefix_import(info.package_prefix, info.app, "actions")
    object_const = object_specs_const(info.model)
    collection_const = collection_specs_const(info.model)

    imports.add_from("ingot.actions", "available_actions")
    imports.add_from(actions_module, object_const, collection_const)
    imports.add_from(session_module, session_name)

    return {
        "object_specs_const": object_const,
        "collection_specs_const": collection_const,
        "session_type": session_name,
    }


def gate_wiring(
    op: OperationConfig,
    resource: ResourceConfig,
    package_prefix: str,
    *,
    is_object_scope: bool,
) -> tuple[dict[str, object], list[tuple[str, str]]]:
    """Wire imports + body context for one op's execution gate.

    Returns ``({}, [])`` when the op has no ``can`` configured.
    Centralized so every CRUD and action op renders an identical
    gate -- visibility predicate and 403 enforcement share the
    one callable, so they can never drift.
    """
    if op.can is None:
        return {}, []

    _, model = Name.from_dotted(resource.model)
    actions_module = prefix_import(
        package_prefix, app_module_for(resource.model), "actions"
    )
    const = (
        object_specs_const(model)
        if is_object_scope
        else collection_specs_const(model)
    )

    return (
        {"gate_specs_const": const, "gate_op_name": op.name},
        [
            ("ingot.actions", "find_can"),
            ("fastapi", "HTTPException"),
            (actions_module, const),
        ],
    )


FETCH_OR_404_IMPORT: tuple[str, str] = (
    "ingot.utils",
    "get_object_from_query_or_404",
)
"""Import pair for the load-or-404 helper.

Get, update, delete, the action op (object-scope branch), and the
permissions endpoint all share this one row-lookup helper -- they
either dump the loaded row, gate on it, or both.  Centralized as
a constant so each op site reads as ``[FETCH_OR_404_IMPORT, ...]``
rather than repeating the literal tuple.
"""


# -------------------------------------------------------------------
# Content renderers -- produce code strings without touching paths or
# imports.  Exposed at module level so tests can exercise them
# directly and so renderers above can reuse them.
# -------------------------------------------------------------------


def render_enum_class(enum: EnumClass) -> str:
    """Render an Enum class definition string.

    Kept as a Python string builder because repr-formatted
    member values aren't something jinja filters express
    cleanly.  Called from ``_enum_fragment`` to pre-render
    the slot value.
    """
    lines = [f"class {enum.name}({enum.base}):"]

    for member_name, member_value in enum.members:
        lines.append(f"    {member_name} = {member_value!r}")

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
