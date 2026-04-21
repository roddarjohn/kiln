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
# Get
# -------------------------------------------------------------------


class FieldsOptions(BaseModel):
    """Options for operations that accept a field list."""

    fields: list[FieldSpec] | None = None


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
            options: Parsed FieldsOptions.

        Returns:
            List of objects (schema, handler, test).

        """
        model, _, pk_name, pk_py_type, _ = _resolve_resource_info(ctx)
        fields = getattr(options, "fields", None)
        out: list[object] = []

        if fields:
            out_fields = _field_dicts(fields)
            out.append(
                SchemaClass(
                    name=model.suffixed("Resource"),
                    fields=out_fields,
                    doc=f"Resource schema for {model.pascal}.",
                )
            )
            out.append(
                SerializerFn(
                    function_name=f"to_{model.lower}_resource",
                    model_name=model.pascal,
                    schema_name=model.suffixed("Resource"),
                    fields=out_fields,
                )
            )

        handler = RouteHandler(
            method="GET",
            path=f"/{{{pk_name}}}",
            function_name=f"get_{model.lower}",
            op_name="get",
            response_model=(model.suffixed("Resource") if fields else None),
            return_type=(model.suffixed("Resource") if fields else "object"),
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
                response_schema=(
                    model.suffixed("Resource") if fields else None
                ),
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
            options: Parsed List.Options.

        Returns:
            List of objects.

        """
        model, _, _, _, _ = _resolve_resource_info(ctx)
        fields = getattr(options, "fields", None)
        out: list[object] = []

        has_resource_schema = bool(fields)
        # Check store for earlier schema from get
        earlier = ctx.store.get("resource", ctx.instance_id, "get")
        for obj in earlier:
            if isinstance(obj, SchemaClass):
                has_resource_schema = True

        has_earlier_serializer = any(
            isinstance(o, SerializerFn) for o in earlier
        )
        if fields and not any(isinstance(o, SchemaClass) for o in earlier):
            out_fields = _field_dicts(fields)
            out.append(
                SchemaClass(
                    name=model.suffixed("Resource"),
                    fields=out_fields,
                    doc=(f"Resource schema for {model.pascal}."),
                )
            )
            if not has_earlier_serializer:
                out.append(
                    SerializerFn(
                        function_name=(f"to_{model.lower}_resource"),
                        model_name=model.pascal,
                        schema_name=model.suffixed("Resource"),
                        fields=out_fields,
                    )
                )

        response_model = (
            f"list[{model.suffixed('Resource')}]"
            if has_resource_schema
            else "list"
        )

        handler = RouteHandler(
            method="GET",
            path="/",
            function_name=f"list_{model.lower}s",
            op_name="list",
            response_model=response_model,
            return_type=(
                f"list[{model.suffixed('Resource')}]"
                if has_resource_schema
                else "object"
            ),
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
