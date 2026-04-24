"""Create operation: POST / -- create a new resource."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from foundry.naming import Name
from foundry.operation import operation
from kiln.config.schema import PYTHON_TYPES
from kiln.operations.types import (
    FieldsOptions,
    RouteHandler,
    RouteParam,
    SchemaClass,
    TestCase,
    _field_dicts,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from foundry.engine import BuildContext
    from kiln.config.schema import OperationConfig, ResourceConfig


@operation(
    "create",
    scope="operation",
    dispatch_on="name",
    requires=["list"],
)
class Create:
    """POST / -- create a new resource."""

    Options = FieldsOptions

    def build(
        self,
        ctx: BuildContext[OperationConfig],
        options: FieldsOptions,
    ) -> Iterable[object]:
        """Produce output for POST /.

        Args:
            ctx: Build context for the ``"create"`` operation entry.
            options: Parsed :class:`FieldsOptions`.

        Yields:
            The ``{Model}CreateRequest`` schema, the route handler,
            and a test case.

        """
        resource = cast(
            "ResourceConfig",
            ctx.store.ancestor_of(ctx.instance_id, "resource"),
        )
        _, model = Name.from_dotted(resource.model)
        request_schema = model.suffixed("CreateRequest")

        yield SchemaClass(
            name=request_schema,
            fields=_field_dicts(options.fields),
            doc=f"Request body for creating a {model.pascal}.",
        )

        yield RouteHandler(
            method="POST",
            path="/",
            function_name=f"create_{model.lower}",
            op_name=ctx.instance.name,
            params=[RouteParam(name="body", annotation=request_schema)],
            status_code=201,
            doc=f"Create a new {model.pascal}.",
            request_schema=request_schema,
            body_template="fastapi/ops/create.py.j2",
            extra_imports=[("sqlalchemy", "insert")],
        )

        yield TestCase(
            op_name="create",
            method="post",
            path="/",
            status_success=201,
            status_invalid=422,
            has_request_body=True,
            request_schema=request_schema,
            request_fields=[
                {"name": f.name, "py_type": PYTHON_TYPES[f.type]}
                for f in options.fields
            ],
        )
