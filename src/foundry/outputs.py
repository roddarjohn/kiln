"""Typed output objects for code generation.

These objects are the output of the build phase.  Operations
produce them; renderers consume them to generate code.  All
types are mutable dataclasses so that later operations can
inspect and modify earlier operations' output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# -------------------------------------------------------------------
# Primitive building blocks
# -------------------------------------------------------------------


@dataclass
class Field:
    """A named, typed field in a schema or parameter list."""

    name: str
    py_type: str
    optional: bool = False


@dataclass
class RouteParam:
    """A parameter on a route handler function."""

    name: str
    annotation: str
    default: str | None = None


# -------------------------------------------------------------------
# Schema types
# -------------------------------------------------------------------


@dataclass
class SchemaClass:
    """A Pydantic model class to be rendered in a schema file.

    Covers response schemas (``UserResource``), request schemas
    (``UserCreateRequest``), and extension schemas (filter nodes,
    sort clauses, page wrappers).
    """

    name: str
    fields: list[Field] = field(default_factory=list)
    base: str = "BaseModel"
    validators: list[str] = field(default_factory=list)
    doc: str | None = None

    def add_field(
        self,
        name: str,
        py_type: str,
        *,
        optional: bool = False,
    ) -> None:
        """Append a field."""
        self.fields.append(
            Field(
                name=name,
                py_type=py_type,
                optional=optional,
            )
        )


@dataclass
class EnumClass:
    """An enum class (e.g. ``SortField``)."""

    name: str
    members: list[tuple[str, str]] = field(default_factory=list)
    base: str = "str, Enum"


# -------------------------------------------------------------------
# Route types
# -------------------------------------------------------------------


@dataclass
class RouteHandler:
    """A single route handler function.

    Produced by CRUD and action operations.  The assembler
    collects all handlers for a resource into one route file.

    The renderer builds the handler's body via :attr:`body_template`
    rendered with :attr:`body_context`; ops that carry the body
    inline set :attr:`body_lines` instead and leave
    :attr:`body_template` ``None``.
    """

    method: str
    path: str
    function_name: str
    params: list[RouteParam] = field(default_factory=list)
    body_param: str | None = None
    request_schema: str | None = None
    response_model: str | None = None
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

    def add_decorator(self, decorator: str) -> None:
        """Append a decorator to the handler."""
        self.decorators.append(decorator)

    def prepend_body(self, line: str) -> None:
        """Insert a line at the start of the function body."""
        self.body_lines.insert(0, line)

    def append_body(self, line: str) -> None:
        """Append a line to the function body."""
        self.body_lines.append(line)


@dataclass
class RouterMount:
    """A sub-router mount in an app or project router.

    The assembler collects these to produce the router
    ``__init__.py`` that includes all sub-routers.
    """

    module: str
    alias: str
    prefix: str | None = None


# -------------------------------------------------------------------
# Serializer types
# -------------------------------------------------------------------


@dataclass
class SerializerFn:
    """A serializer function that maps a model to a schema.

    E.g. ``def to_user_resource(row) -> UserResource``.
    """

    function_name: str
    model_name: str
    schema_name: str
    fields: list[Field] = field(default_factory=list)


# -------------------------------------------------------------------
# Test types
# -------------------------------------------------------------------


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


# -------------------------------------------------------------------
# Static / template-rendered files
# -------------------------------------------------------------------


@dataclass
class StaticFile:
    """A file rendered directly from a template.

    Used for scaffold files (auth, db sessions), utils, and
    other files that don't need the assembler's multi-contributor
    merging.
    """

    path: str
    template: str
    context: dict[str, Any] = field(default_factory=dict)
