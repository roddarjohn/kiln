"""Build-output dataclasses and the helpers that construct them.

These are the vocabulary of emissions every ``@operation``-decorated
class draws from: schema classes, route handlers, serializer
functions, test cases, etc.  Registered renderers in
:mod:`kiln.operations.renderers` consume them to produce Python
code.  All are mutable dataclasses so later operations can inspect
and modify earlier operations' output.

They live in kiln rather than foundry because they're
FastAPI/Pydantic-flavored — a non-Python target wouldn't use them.
:class:`foundry.outputs.StaticFile` stays in foundry since "render
this template to this path" is target-agnostic.

The small constructor helpers at the bottom (``FieldsOptions``,
``_field_dicts``, ``_construct_response_schema``,
``_construct_serializer``) live here too because they're tightly
coupled to the dataclasses above — a read op always pairs its
``SchemaClass`` and its ``SerializerFn``, and the config-to-Field
conversion is the same everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel

from foundry.naming import Name, split_dotted_class
from kiln.config.schema import (
    PYTHON_TYPES,
    FieldSpec,
    FieldType,
    LoaderStrategy,
)

_LOADER_FN: dict[LoaderStrategy, str] = {
    "selectin": "selectinload",
    "joined": "joinedload",
    "subquery": "subqueryload",
}
"""Map of config load-strategy tokens to the ``sqlalchemy.orm``
function name that implements them.  ``raiseload`` / ``noload`` are
deliberately omitted: a field the user has configured for nested
dumping must be loadable."""


@dataclass
class Field:
    """A named, typed field in a schema or parameter list.

    When ``nested_serializer`` is set, this field is a dump of a
    related model: the generated serializer calls the named function
    on ``obj.{name}`` (or list-comprehends over it when ``many`` is
    true) rather than assigning the attribute directly.
    """

    name: str
    py_type: str
    optional: bool = False
    nested_serializer: str | None = None
    """Function name of the sub-serializer that maps the related
    model instance to the nested schema.  ``None`` for scalar fields."""
    many: bool = False
    """When ``True`` with ``nested_serializer`` set, the source
    attribute is a collection and each element is serialized through
    ``nested_serializer``."""
    nested_fields: list[Field] | None = None
    """Sub-field list when this is a nested field.  Carried through
    so renderers can walk the full nested structure (e.g. generated
    test fixtures need to populate scalar-nested paths like
    ``row.author.id`` on the mock ORM row)."""


@dataclass
class RouteParam:
    """A parameter on a route handler function."""

    name: str
    annotation: str
    default: str | None = None


@dataclass
class SchemaClass:
    """A Pydantic model class to be rendered in a schema file.

    Two rendering modes, analogous to :class:`RouteHandler`:

    - **Flat-field mode** (default): ``fields`` + ``validators``
      are rendered through the default schema-class template.
      Used for response/request schemas with a regular
      ``name: type`` list (``UserResource``, ``UserCreateRequest``).
    - **Templated mode**: when ``body_template`` is set, the
      renderer dispatches to that template with ``body_context``
      and ignores ``fields`` / ``validators``.  Used for schemas
      with conditional fields, aliases, or trailing boilerplate
      that the flat path can't express — e.g. list-op filter
      nodes, sort clauses, search requests, paged responses.
    """

    name: str
    fields: list[Field] = field(default_factory=list)
    base: str = "BaseModel"
    validators: list[str] = field(default_factory=list)
    doc: str | None = None
    body_template: str | None = None
    body_context: dict[str, Any] = field(default_factory=dict)
    extra_imports: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class EnumClass:
    """An enum class (e.g. ``SortField``)."""

    name: str
    members: list[tuple[str, str]] = field(default_factory=list)
    base: str = "str, Enum"


@dataclass
class RouteHandler:
    """A single route handler function, produced by a CRUD or action op.

    Body dispatches through :attr:`body_template` + :attr:`body_context`;
    ops that inline their body set :attr:`body_lines` and leave the
    template ``None``.

    :attr:`request_schema_module` / :attr:`response_schema_module`
    override the import source: ``None`` resolves to the generated
    schemas module (CRUD); the action op sets them to the consumer's
    module so introspected user types import from their real location.

    :attr:`op_name` carries the
    :class:`~kiln.config.schema.OperationConfig` ``name`` so
    :class:`~kiln.operations.auth.Auth` can filter per-op
    ``require_auth`` overrides.
    """

    method: str
    path: str
    function_name: str
    op_name: str = ""
    params: list[RouteParam] = field(default_factory=list)
    body_param: str | None = None
    request_schema: str | None = None
    request_schema_module: str | None = None
    response_model: str | None = None
    response_schema_module: str | None = None
    serializer_fn: str | None = None
    status_code: int | None = None
    return_type: str | None = None
    body_lines: list[str] = field(default_factory=list)
    body_template: str | None = None
    body_context: dict[str, object] = field(default_factory=dict)
    decorators: list[str] = field(default_factory=list)
    doc: str | None = None
    extra_deps: list[str] = field(default_factory=list)
    extra_imports: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RouterMount:
    """A sub-router mount in an app or project router.

    The assembler collects these to produce the router
    ``__init__.py`` that includes all sub-routers.
    """

    module: str
    alias: str
    prefix: str | None = None


@dataclass
class SerializerFn:
    """A serializer function that maps a model to a schema.

    E.g. ``def to_user_resource(row) -> UserResource``.

    ``model_module`` is the dotted import path to the SQLAlchemy
    model class.  For top-level serializers this matches the
    resource's ``model`` path; for nested sub-serializers it points
    at the related model so the renderer can import it alongside
    the parent.
    """

    function_name: str
    model_name: str
    model_module: str
    schema_name: str
    fields: list[Field] = field(default_factory=list)


@dataclass
class TestCase:
    """Metadata for a generated test function."""

    __test__ = False  # prevent pytest collection

    op_name: str
    method: str
    path: str
    status_success: int
    status_not_found: int | None = None
    status_invalid: int | None = None
    requires_auth: bool = False
    has_request_body: bool = False
    request_schema: str | None = None
    request_fields: list[dict[str, str]] = field(
        default_factory=list,
    )
    response_schema: str | None = None
    is_list_response: bool = False
    action_name: str | None = None


class FieldsOptions(BaseModel):
    """Options for operations that require a field list."""

    fields: list[FieldSpec]


def _field_dicts(fields: list[FieldSpec]) -> list[Field]:
    """Convert scalar ``FieldSpec`` entries to :class:`Field` dataclasses.

    Write-op request schemas (``create`` / ``update``) call this —
    they don't support nested fields today, so any nested entry is
    rejected here rather than silently producing an invalid schema.
    """
    out: list[Field] = []

    for f in fields:
        if f.is_nested:
            msg = (
                f"Field {f.name!r}: nested fields are only supported "
                f"on read operations (get, list)."
            )
            raise ValueError(msg)

        out.append(
            Field(name=f.name, py_type=PYTHON_TYPES[cast("FieldType", f.type)])
        )

    return out


@dataclass
class _DumpOutputs:
    """Artifacts produced by :func:`_construct_dump`.

    Read ops yield ``main_schema``, ``main_serializer``, then every
    entry in ``nested_schemas`` and ``nested_serializers`` — the
    nested lists are ordered deepest-first so classes are defined
    before the parent schema that references them.

    ``load_options`` carries the SQLAlchemy loader-chain strings the
    handler's ``select(...)`` needs (e.g.
    ``"selectinload(Task.project).selectinload(Project.owner)"``)
    so nested relationships are eagerly loaded before the serializer
    reads them.  ``load_imports`` are the ``(module, name)`` pairs
    those chains reference — loader functions plus any related model
    classes — to be added to the handler's ``extra_imports``.
    """

    main_schema: SchemaClass
    main_serializer: SerializerFn
    nested_schemas: list[SchemaClass]
    nested_serializers: list[SerializerFn]
    load_options: list[str]
    load_imports: list[tuple[str, str]]


def _construct_dump(
    model: Name,
    model_module: str,
    fields: list[FieldSpec],
    suffix: str,
    stem: str,
) -> _DumpOutputs:
    """Build the schema + serializer pair for a read op, expanding nesting.

    ``suffix`` is appended to the model's pascal-cased name to form
    the main schema class (``"Resource"`` -> ``"UserResource"``).
    ``stem`` becomes the trailing segment of the serializer name
    (``"resource"`` -> ``"to_user_resource"``).  Nested fields
    recurse, emitting additional ``SchemaClass`` / ``SerializerFn``
    entries whose names are derived from the accumulated field path
    (e.g. ``TaskResourceProjectNested`` /
    ``to_task_resource_project_nested``).
    """
    main_schema_name = model.suffixed(suffix)
    main_fn_name = f"to_{model.lower}_{stem}"
    expanded, nested_schemas, nested_sers = _expand_field_specs(
        fields,
        class_prefix=main_schema_name,
        fn_prefix=main_fn_name,
    )
    main_schema = SchemaClass(
        name=main_schema_name,
        fields=expanded,
        doc=f"{suffix} schema for {model.pascal}.",
    )
    main_serializer = SerializerFn(
        function_name=main_fn_name,
        model_name=model.pascal,
        model_module=model_module,
        schema_name=main_schema.name,
        fields=expanded,
    )
    load_options, load_imports = _build_load_chains(fields, model.pascal)
    return _DumpOutputs(
        main_schema=main_schema,
        main_serializer=main_serializer,
        nested_schemas=nested_schemas,
        nested_serializers=nested_sers,
        load_options=load_options,
        load_imports=load_imports,
    )


def _expand_field_specs(
    specs: list[FieldSpec],
    class_prefix: str,
    fn_prefix: str,
) -> tuple[list[Field], list[SchemaClass], list[SerializerFn]]:
    """Walk ``specs``, expanding nested entries into sub-artifacts.

    Returns ``(fields, nested_schemas, nested_serializers)``:

    * ``fields`` is the flat :class:`Field` list the caller drops
      into its own schema and serializer.  Scalar specs become plain
      ``Field``; nested specs become ``Field`` entries carrying the
      nested class as ``py_type`` and the sub-serializer's function
      name in ``nested_serializer``.
    * ``nested_schemas`` / ``nested_serializers`` are the sub-dump
      artifacts, ordered deepest-first so they render before the
      parent that references them.

    Naming uses the accumulated path to guarantee uniqueness without
    an explicit alias: ``TaskResource`` + field ``project`` ->
    ``TaskResourceProjectNested``, and a further nested ``owner``
    inside that -> ``TaskResourceProjectOwnerNested``.
    """
    fields: list[Field] = []
    out_schemas: list[SchemaClass] = []
    out_sers: list[SerializerFn] = []

    for fs in specs:
        if not fs.is_nested:
            fields.append(
                Field(
                    name=fs.name,
                    py_type=PYTHON_TYPES[cast("FieldType", fs.type)],
                )
            )
            continue

        # The FieldSpec validator guarantees non-None here when nested.
        fs_model = cast("str", fs.model)
        fs_fields = cast("list[FieldSpec]", fs.fields)
        field_pascal = Name(fs.name).pascal
        child_class_prefix = f"{class_prefix}{field_pascal}"
        child_fn_prefix = f"{fn_prefix}_{fs.name}"

        inner_fields, inner_schemas, inner_sers = _expand_field_specs(
            fs_fields,
            class_prefix=child_class_prefix,
            fn_prefix=child_fn_prefix,
        )

        nested_schema_name = f"{child_class_prefix}Nested"
        nested_fn_name = f"{child_fn_prefix}_nested"
        related_module, related_class = split_dotted_class(fs_model)
        related_pascal = Name(related_class).pascal

        nested_schema = SchemaClass(
            name=nested_schema_name,
            fields=inner_fields,
            doc=f"Nested {related_pascal} dump under {class_prefix}.",
        )
        nested_serializer = SerializerFn(
            function_name=nested_fn_name,
            model_name=related_pascal,
            model_module=related_module,
            schema_name=nested_schema_name,
            fields=inner_fields,
        )

        out_schemas.extend(inner_schemas)
        out_schemas.append(nested_schema)
        out_sers.extend(inner_sers)
        out_sers.append(nested_serializer)

        py_type = (
            f"list[{nested_schema_name}]" if fs.many else nested_schema_name
        )
        fields.append(
            Field(
                name=fs.name,
                py_type=py_type,
                nested_serializer=nested_fn_name,
                many=fs.many,
                nested_fields=inner_fields,
            )
        )

    return fields, out_schemas, out_sers


def _build_load_chains(
    specs: list[FieldSpec],
    parent_pascal: str,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Build eager-load chain strings for every nested field in ``specs``.

    Returns ``(chains, imports)``:

    * ``chains`` is a list of expressions to drop into
      ``select(...).options(...)``.  One chain is produced per leaf
      nested path — a chain to an intermediate would be redundant
      since ``selectinload(A.b).selectinload(B.c)`` already loads
      ``A.b``.
    * ``imports`` are ``(module, class_or_fn)`` pairs the chain
      strings reference: the loader functions themselves and any
      related model classes used at intermediate levels.  The parent
      model class is intentionally omitted — the caller already
      imports it for its own ``select(...)`` statement.

    Load strategy mixes per level: ``"joined"`` on the outer and
    ``"selectin"`` on the inner compose as
    ``joinedload(A.b).selectinload(B.c)``.
    """
    chains: list[str] = []
    imports: list[tuple[str, str]] = []

    for fs in specs:
        if not fs.is_nested:
            continue

        fs_model = cast("str", fs.model)
        fs_fields = cast("list[FieldSpec]", fs.fields)

        loader_fn = _LOADER_FN[fs.load]
        imports.append(("sqlalchemy.orm", loader_fn))

        head = f"{loader_fn}({parent_pascal}.{fs.name})"

        related_module, related_class = split_dotted_class(fs_model)
        related_pascal = Name(related_class).pascal

        has_nested_child = any(child.is_nested for child in fs_fields)

        if not has_nested_child:
            chains.append(head)
            continue

        # Intermediate level: the related model class is referenced in
        # the inner chain segments, so it has to be imported.
        imports.append((related_module, related_pascal))
        inner_chains, inner_imports = _build_load_chains(
            fs_fields, related_pascal
        )
        imports.extend(inner_imports)
        chains.extend(f"{head}.{inner}" for inner in inner_chains)

    return chains, imports
