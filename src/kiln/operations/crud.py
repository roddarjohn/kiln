"""CRUD operations: Get, List, Create, Update, Delete.

Each operation produces typed output objects (RouteHandler,
SchemaClass, TestCase, etc.) that the assembler later renders
into generated files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import (
    Field,
    RouteHandler,
    RouteParam,
    SchemaClass,
    SerializerFn,
    TestCase,
)
from kiln.config.schema import FieldSpec  # noqa: TC001
from kiln.generators._helpers import PYTHON_TYPES
from kiln.operations._list_config import (  # noqa: TC001
    FilterConfig,
    OrderConfig,
    PaginateConfig,
)

if TYPE_CHECKING:
    from foundry.engine import BuildContext


# -------------------------------------------------------------------
# Shared helpers
# -------------------------------------------------------------------


def _field_dicts(fields: list[FieldSpec]) -> list[Field]:
    """Convert config FieldSpecs to Fields."""
    return [
        Field(
            name=f.name,
            py_type=PYTHON_TYPES[f.type],
        )
        for f in fields
    ]


def _resolve_resource_info(
    ctx: BuildContext,
) -> tuple[Name, str, str, str, str]:
    """Extract common resource info from the build context.

    Returns:
        Tuple of ``(model, model_module, pk_name,
        pk_py_type, route_prefix)``.

    """
    resource = ctx.instance
    model_module, model = Name.from_dotted(resource.model)
    pk_name = resource.pk
    pk_py_type = PYTHON_TYPES[resource.pk_type]
    route_prefix = resource.route_prefix or f"/{model.lower}s"
    return (
        model,
        model_module,
        pk_name,
        pk_py_type,
        route_prefix,
    )


# -------------------------------------------------------------------
# Read-op helpers
# -------------------------------------------------------------------


class FieldsOptions(BaseModel):
    """Options for operations that accept a field list."""

    fields: list[FieldSpec] | None = None


def _read_schema_outputs(
    model: Name,
    fields: list[FieldSpec],
    suffix: str,
    serializer_stem: str,
) -> tuple[SchemaClass, SerializerFn]:
    """Build the ``SchemaClass`` / ``SerializerFn`` pair for a read op.

    ``suffix`` is appended to the model's pascal-cased name to form
    the response schema class (e.g. ``Resource`` → ``UserResource``).
    ``serializer_stem`` becomes the trailing segment of the
    serializer function, e.g. ``resource`` → ``to_user_resource``.
    """
    out_fields = _field_dicts(fields)
    schema_name = model.suffixed(suffix)
    serializer_fn = f"to_{model.lower}_{serializer_stem}"
    schema = SchemaClass(
        name=schema_name,
        fields=out_fields,
        doc=f"{suffix} schema for {model.pascal}.",
    )
    serializer = SerializerFn(
        function_name=serializer_fn,
        model_name=model.pascal,
        schema_name=schema_name,
        fields=out_fields,
    )
    return schema, serializer


# -------------------------------------------------------------------
# Get
# -------------------------------------------------------------------


@operation("get", scope="resource")
class Get:
    """GET /{pk} -- retrieve a single resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext,
        options: BaseModel,
    ) -> list[object]:
        """Produce output for GET /{pk}.

        Args:
            ctx: Build context with resource config.
            options: Parsed :class:`FieldsOptions`.

        Returns:
            List of objects (handler, test, and -- when fields
            are supplied -- the schema + serializer).

        """
        model, _, pk_name, pk_py_type, _ = _resolve_resource_info(ctx)
        fields = getattr(options, "fields", None)
        out: list[object] = []

        schema_name: str | None = None
        serializer_fn: str | None = None
        if fields:
            schema, serializer = _read_schema_outputs(
                model, fields, "Resource", "resource"
            )
            schema_name = schema.name
            serializer_fn = serializer.function_name
            out.extend([schema, serializer])

        handler = RouteHandler(
            method="GET",
            path=f"/{{{pk_name}}}",
            function_name=f"get_{model.lower}",
            op_name="get",
            response_model=schema_name,
            serializer_fn=serializer_fn,
            return_type=schema_name or "object",
            doc=f"Get a {model.pascal} by {pk_name}.",
        )
        handler.params.append(RouteParam(name=pk_name, annotation=pk_py_type))
        out.append(handler)

        out.append(
            TestCase(
                op_name="get",
                method="get",
                path=f"/{{{pk_name}}}",
                status_success=200,
                status_not_found=404,
                response_schema=schema_name,
            )
        )

        return out


# -------------------------------------------------------------------
# List
# -------------------------------------------------------------------


@operation("list", scope="resource", requires=["get"])
class List:
    """GET / -- list all resources."""

    class Options(BaseModel):
        """Options for the list operation."""

        fields: list[FieldSpec] | None = None
        filters: FilterConfig | None = None
        ordering: OrderConfig | None = None
        pagination: PaginateConfig | None = None

    def build(
        self,
        ctx: BuildContext,
        options: BaseModel,
    ) -> list[object]:
        """Produce output for GET / (or POST /search with filters).

        Args:
            ctx: Build context with resource config.
            options: Parsed ``Options``.

        Returns:
            List of objects (handler, test, and -- when fields
            are supplied -- a distinct ``{Model}ListItem`` schema
            + serializer).

        """
        model, _, _, _, _ = _resolve_resource_info(ctx)
        fields = getattr(options, "fields", None)
        out: list[object] = []

        schema_name: str | None = None
        serializer_fn: str | None = None
        if fields:
            schema, serializer = _read_schema_outputs(
                model, fields, "ListItem", "list_item"
            )
            schema_name = schema.name
            serializer_fn = serializer.function_name
            out.extend([schema, serializer])

        handler = RouteHandler(
            method="GET",
            path="/",
            function_name=f"list_{model.lower}s",
            op_name="list",
            response_model=f"list[{schema_name}]" if schema_name else "list",
            serializer_fn=serializer_fn,
            return_type=(f"list[{schema_name}]" if schema_name else "object"),
            doc=f"List all {model.pascal} records.",
        )
        out.append(handler)

        out.append(
            TestCase(
                op_name="list",
                method="get",
                path="/",
                status_success=200,
                is_list_response=True,
            )
        )

        return out


# -------------------------------------------------------------------
# Create
# -------------------------------------------------------------------


@operation("create", scope="resource", requires=["list"])
class Create:
    """POST / -- create a new resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext,
        options: BaseModel,
    ) -> list[object]:
        """Produce output for POST /.

        Args:
            ctx: Build context with resource config.
            options: Parsed FieldsOptions.

        Returns:
            List of objects.

        """
        model, _, _, _, _ = _resolve_resource_info(ctx)
        fields = getattr(options, "fields", None)
        out: list[object] = []

        if fields:
            out_fields = _field_dicts(fields)
            out.append(
                SchemaClass(
                    name=model.suffixed("CreateRequest"),
                    fields=out_fields,
                    doc=(f"Request body for creating a {model.pascal}."),
                )
            )

        handler = RouteHandler(
            method="POST",
            path="/",
            function_name=f"create_{model.lower}",
            op_name="create",
            status_code=201,
            doc=f"Create a new {model.pascal}.",
            request_schema=(
                model.suffixed("CreateRequest") if fields else None
            ),
        )
        out.append(handler)

        out.append(
            TestCase(
                op_name="create",
                method="post",
                path="/",
                status_success=201,
                status_invalid=422 if fields else None,
                has_request_body=bool(fields),
                request_schema=(
                    model.suffixed("CreateRequest") if fields else None
                ),
            )
        )

        return out


# -------------------------------------------------------------------
# Update
# -------------------------------------------------------------------


@operation("update", scope="resource", requires=["create"])
class Update:
    """PATCH /{pk} -- partially update a resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext,
        options: BaseModel,
    ) -> list[object]:
        """Produce output for PATCH /{pk}.

        Args:
            ctx: Build context with resource config.
            options: Parsed FieldsOptions.

        Returns:
            List of objects.

        """
        model, _, pk_name, pk_py_type, _ = _resolve_resource_info(ctx)
        fields = getattr(options, "fields", None)
        out: list[object] = []

        if fields:
            out_fields = _field_dicts(fields)
            out.append(
                SchemaClass(
                    name=model.suffixed("UpdateRequest"),
                    fields=[
                        Field(
                            name=f.name,
                            py_type=f.py_type,
                            optional=True,
                        )
                        for f in out_fields
                    ],
                    doc=(f"Request body for updating a {model.pascal}."),
                )
            )

        handler = RouteHandler(
            method="PATCH",
            path=f"/{{{pk_name}}}",
            function_name=f"update_{model.lower}",
            op_name="update",
            doc=f"Update a {model.pascal} by {pk_name}.",
            request_schema=(
                model.suffixed("UpdateRequest") if fields else None
            ),
        )
        handler.params.append(RouteParam(name=pk_name, annotation=pk_py_type))
        out.append(handler)

        out.append(
            TestCase(
                op_name="update",
                method="patch",
                path=f"/{{{pk_name}}}",
                status_success=200,
                status_not_found=404,
                status_invalid=422 if fields else None,
                has_request_body=bool(fields),
                request_schema=(
                    model.suffixed("UpdateRequest") if fields else None
                ),
            )
        )

        return out


# -------------------------------------------------------------------
# Delete
# -------------------------------------------------------------------


@operation("delete", scope="resource", requires=["update"])
class Delete:
    """DELETE /{pk} -- delete a resource."""

    def build(
        self,
        ctx: BuildContext,
        _options: BaseModel,
    ) -> list[object]:
        """Produce output for DELETE /{pk}.

        Args:
            ctx: Build context with resource config.
            _options: Unused.

        Returns:
            List of objects.

        """
        model, _, pk_name, pk_py_type, _ = _resolve_resource_info(ctx)

        handler = RouteHandler(
            method="DELETE",
            path=f"/{{{pk_name}}}",
            function_name=f"delete_{model.lower}",
            op_name="delete",
            status_code=204,
            doc=f"Delete a {model.pascal} by {pk_name}.",
        )
        handler.params.append(RouteParam(name=pk_name, annotation=pk_py_type))

        test = TestCase(
            op_name="delete",
            method="delete",
            path=f"/{{{pk_name}}}",
            status_success=204,
            status_not_found=404,
        )

        return [handler, test]
