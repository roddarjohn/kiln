"""Resource generation pipeline.

Composes :class:`~kiln.generators.base.FileSpec` objects and runs
:class:`~kiln.generators.fastapi.operations.Operation` classes
against them to produce the final generated files.

The pipeline is the main extension point for customising FastAPI
code generation.  Pass a custom list of operations to
:class:`ResourcePipeline` to add, remove, or replace CRUD
behaviour::

    from kiln.generators.fastapi.operations import (
        default_operations,
    )
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    ops = default_operations()
    ops.append(MyBulkCreateOperation())
    pipeline = ResourcePipeline(operations=ops)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.config.schema import FieldsConfig
from kiln.generators._helpers import (
    PYTHON_TYPES,
    ImportCollector,
    Name,
    prefix_import,
    resolve_db_session,
)
from kiln.generators.base import FileSpec, GeneratedFile
from kiln.generators.fastapi.operations import (
    Operation,
    SharedContext,
    _field_dicts,
    default_operations,
)

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig, ResourceConfig


class ResourcePipeline:
    """Composable pipeline that builds files for one resource.

    For each resource the pipeline:

    1. Creates empty :class:`FileSpec` objects for the schema,
       serializer, and route files.
    2. Runs every enabled :class:`Operation` against the specs,
       letting each append its imports, schema classes, and route
       handlers.
    3. Auto-wires cross-file imports (route imports from schema,
       route imports from serializer).
    4. Renders each spec to a :class:`GeneratedFile`.

    Args:
        operations: Ordered list of operations to run.  Defaults
            to :func:`default_operations`.

    """

    def __init__(  # noqa: D107
        self,
        operations: list[Operation] | None = None,
    ) -> None:
        self.operations = (
            operations if operations is not None else default_operations()
        )

    def build(
        self,
        resource: ResourceConfig,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Build all generated files for a single *resource*.

        Args:
            resource: The resource configuration.
            config: The top-level kiln configuration.

        Returns:
            List of :class:`GeneratedFile` objects (schema,
            optional serializer, routes).

        """
        model_module, model = Name.from_dotted(resource.model)
        app = config.module
        pkg = config.package_prefix

        session_module, get_db_fn = resolve_db_session(
            resource.db_key, config.databases
        )
        route_prefix = resource.route_prefix or f"/{model.lower}s"

        has_resource_schema = _will_have_resource_schema(resource)
        response_schema = (
            model.suffixed("Resource") if has_resource_schema else None
        )

        ctx = SharedContext(
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
            package_prefix=pkg,
        )

        # Create file specs
        schema_spec = _init_schema(model, app, pkg)
        serializer_spec = _init_serializer(
            model, model_module, app, pkg, resource
        )
        route_spec = _init_route(model, app, pkg, ctx)

        # Run operations
        for op in self.operations:
            if op.enabled(resource):
                op.contribute_schema(schema_spec, resource, ctx)
                op.contribute_route(route_spec, resource, ctx)

        # Auto-wire cross-file imports
        _wire_imports(
            schema_spec,
            serializer_spec,
            route_spec,
            has_resource_schema,
        )

        # Render
        files = [schema_spec.render()]
        if has_resource_schema:
            files.append(serializer_spec.render())
        files.append(route_spec.render())
        return files


# -------------------------------------------------------------------
# FileSpec initialisation (module-level for testability)
# -------------------------------------------------------------------


def _init_schema(model: Name, app: str, pkg: str) -> FileSpec:
    """Create the schema FileSpec with base imports."""
    spec = FileSpec(
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


def _init_serializer(
    model: Name,
    model_module: str,
    app: str,
    pkg: str,
    resource: ResourceConfig,
) -> FileSpec:
    """Create the serializer FileSpec with base imports."""
    # Determine resource_fields from get or list config
    resource_fields: list[dict[str, str]] = []
    if isinstance(resource.get, FieldsConfig):
        resource_fields = _field_dicts(resource.get.fields)
    elif isinstance(resource.list, FieldsConfig):
        resource_fields = _field_dicts(resource.list.fields)

    spec = FileSpec(
        path=f"{app}/serializers/{model.lower}.py",
        template="fastapi/serializer_outer.py.j2",
        imports=ImportCollector(),
        exports=[f"to_{model.lower}_resource"],
        package_prefix=pkg,
        context={
            "model_name": model.pascal,
            "model_lower": model.lower,
            "resource_fields": resource_fields,
        },
    )
    spec.imports.add_from("__future__", "annotations")
    spec.imports.add_from(model_module, model.pascal)
    return spec


def _init_route(
    model: Name,
    app: str,
    pkg: str,
    ctx: SharedContext,
) -> FileSpec:
    """Create the route FileSpec with base imports."""
    utils_module = prefix_import(pkg, "utils")
    spec = FileSpec(
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

    # PK type imports
    if "uuid" in ctx.pk_py_type:
        spec.imports.add("uuid")

    spec.imports.add_from("typing", "Annotated")
    spec.imports.add_from("fastapi", "APIRouter", "Depends", "status")
    spec.imports.add_from("sqlalchemy.ext.asyncio", "AsyncSession")

    # Auth import
    if ctx.has_auth:
        spec.imports.add_from("auth.dependencies", "get_current_user")

    spec.imports.add_from(ctx.session_module, ctx.get_db_fn)
    return spec


def _will_have_resource_schema(
    resource: ResourceConfig,
) -> bool:
    """Check if get or list have explicit fields."""
    return isinstance(resource.get, FieldsConfig) or isinstance(
        resource.list, FieldsConfig
    )


def _wire_imports(
    schema_spec: FileSpec,
    serializer_spec: FileSpec,
    route_spec: FileSpec,
    has_resource_schema: bool,  # noqa: FBT001
) -> None:
    """Wire cross-file imports between specs."""
    # Route imports from schema
    if schema_spec.exports:
        route_spec.imports.add_from(schema_spec.module, *schema_spec.exports)

    if has_resource_schema:
        # Serializer imports from schema
        resource_cls = serializer_spec.context["model_name"] + "Resource"
        serializer_spec.imports.add_from(schema_spec.module, resource_cls)
        # Route imports from serializer
        route_spec.imports.add_from(
            serializer_spec.module, *serializer_spec.exports
        )
