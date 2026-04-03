"""Pluggable CRUD operations for the resource pipeline.

Each operation class contributes imports, schema classes, and route
handlers to :class:`~kiln.generators.base.FileSpec` objects.
Extensions can add, replace, or remove operations to customize
the generated output.

Example — adding a custom operation::

    from kiln.generators.fastapi.operations import (
        Operation,
        default_operations,
    )
    from kiln.generators.fastapi.pipeline import ResourcePipeline

    class BulkCreateOperation:
        name = "bulk_create"

        def enabled(self, resource):
            return resource.create is not False

        def contribute_schema(self, spec, resource, ctx):
            ...

        def contribute_route(self, spec, resource, ctx):
            ...

    pipeline = ResourcePipeline(
        operations=[*default_operations(), BulkCreateOperation()]
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from kiln.config.schema import FieldsConfig
from kiln.generators._env import render_snippet
from kiln.generators._helpers import PYTHON_TYPES, ImportCollector, Name

if TYPE_CHECKING:
    from kiln.config.schema import ResourceConfig
    from kiln.generators.base import FileSpec


# -------------------------------------------------------------------
# Shared context
# -------------------------------------------------------------------


@dataclass
class SharedContext:
    """Shared state passed to every operation.

    Contains the resolved values that most operations need but
    that come from the overall resource/config, not from a
    single operation.
    """

    model: Name
    model_module: str
    pk_name: str
    pk_py_type: str
    route_prefix: str
    has_auth: bool
    get_db_fn: str
    session_module: str
    has_resource_schema: bool
    response_schema: str | None
    package_prefix: str


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _field_dicts(fields: list) -> list[dict[str, str]]:
    """Convert FieldSpec list to template-ready dicts."""
    return [{"name": f.name, "py_type": PYTHON_TYPES[f.type]} for f in fields]


def _add_field_type_imports(imports: ImportCollector, fields: list) -> None:
    """Add type-specific imports for *fields* to *imports*."""
    for f in fields:
        ft = f.type
        if ft == "uuid":
            imports.add("uuid")
        elif ft == "datetime":
            imports.add_from("datetime", "datetime")
        elif ft == "date":
            imports.add_from("datetime", "date")
        elif ft == "json":
            imports.add_from("typing", "Any")


def _op_requires_auth(resource: ResourceConfig, op_name: str) -> bool:
    """Return whether *op_name* requires authentication."""
    if isinstance(resource.require_auth, bool):
        return resource.require_auth
    return op_name in resource.require_auth


# -------------------------------------------------------------------
# Operation protocol
# -------------------------------------------------------------------


@runtime_checkable
class Operation(Protocol):
    """Protocol for pluggable pipeline operations.

    Each operation can contribute schema classes and route
    handlers to the corresponding :class:`FileSpec` objects.
    Implementations mutate the specs in place — appending to
    ``spec.imports``, ``spec.exports``, and
    ``spec.context["schema_classes"]`` or
    ``spec.context["route_handlers"]``.
    """

    name: str

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True if this operation applies to *resource*."""
        ...

    def contribute_schema(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Add schema classes and imports to *spec*."""
        ...

    def contribute_route(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Add route handlers and imports to *spec*."""
        ...


# -------------------------------------------------------------------
# Built-in operations
# -------------------------------------------------------------------


class GetOperation:
    """GET /{pk} — retrieve a single resource by primary key."""

    name = "get"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when get is not disabled."""
        return resource.get is not False

    def contribute_schema(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit ``{Model}Resource`` schema when fields are explicit."""
        if not isinstance(resource.get, FieldsConfig):
            return
        fields = _field_dicts(resource.get.fields)
        snippet = render_snippet(
            "fastapi/schema_parts/resource.py.j2",
            model_name=ctx.model.pascal,
            fields=fields,
        )
        spec.context["schema_classes"].append(snippet)
        spec.exports.append(ctx.model.suffixed("Resource"))
        _add_field_type_imports(spec.imports, resource.get.fields)

    def contribute_route(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit the GET /{pk} handler."""
        spec.imports.add_from("sqlalchemy", "select")
        spec.imports.add_from(ctx.model_module, ctx.model.pascal)
        spec.imports.add_from(
            spec.context["utils_module"],
            "get_object_from_query_or_404",
        )
        handler = render_snippet(
            "fastapi/ops/get.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            pk_py_type=ctx.pk_py_type,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "get"),
            has_resource_schema=ctx.has_resource_schema,
        )
        spec.context["route_handlers"].append(handler)


class ListOperation:
    """GET / — list all resources."""

    name = "list"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when list is not disabled."""
        return resource.list is not False

    def contribute_schema(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit ``{Model}Resource`` if get didn't already."""
        if not isinstance(resource.list, FieldsConfig):
            return
        if ctx.model.suffixed("Resource") in spec.exports:
            return
        fields = _field_dicts(resource.list.fields)
        snippet = render_snippet(
            "fastapi/schema_parts/resource.py.j2",
            model_name=ctx.model.pascal,
            fields=fields,
        )
        spec.context["schema_classes"].append(snippet)
        spec.exports.append(ctx.model.suffixed("Resource"))
        _add_field_type_imports(spec.imports, resource.list.fields)

    def contribute_route(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit the GET / handler."""
        spec.imports.add_from("sqlalchemy", "select")
        spec.imports.add_from(ctx.model_module, ctx.model.pascal)
        handler = render_snippet(
            "fastapi/ops/list.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "list"),
            has_resource_schema=ctx.has_resource_schema,
        )
        spec.context["route_handlers"].append(handler)


class CreateOperation:
    """POST / — create a new resource."""

    name = "create"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when create is not disabled."""
        return resource.create is not False

    def contribute_schema(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit ``{Model}CreateRequest`` schema."""
        if not isinstance(resource.create, FieldsConfig):
            return
        fields = _field_dicts(resource.create.fields)
        snippet = render_snippet(
            "fastapi/schema_parts/create.py.j2",
            model_name=ctx.model.pascal,
            route_prefix=ctx.route_prefix,
            fields=fields,
        )
        spec.context["schema_classes"].append(snippet)
        spec.exports.append(ctx.model.suffixed("CreateRequest"))
        _add_field_type_imports(spec.imports, resource.create.fields)

    def contribute_route(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit the POST / handler."""
        spec.imports.add_from("sqlalchemy", "insert")
        spec.imports.add_from(ctx.model_module, ctx.model.pascal)
        handler = render_snippet(
            "fastapi/ops/create.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "create"),
            has_schema=isinstance(resource.create, FieldsConfig),
            response_schema=ctx.response_schema,
        )
        spec.context["route_handlers"].append(handler)


class UpdateOperation:
    """PATCH /{pk} — partially update a resource."""

    name = "update"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when update is not disabled."""
        return resource.update is not False

    def contribute_schema(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit ``{Model}UpdateRequest`` schema."""
        if not isinstance(resource.update, FieldsConfig):
            return
        fields = _field_dicts(resource.update.fields)
        snippet = render_snippet(
            "fastapi/schema_parts/update.py.j2",
            model_name=ctx.model.pascal,
            route_prefix=ctx.route_prefix,
            fields=fields,
        )
        spec.context["schema_classes"].append(snippet)
        spec.exports.append(ctx.model.suffixed("UpdateRequest"))
        _add_field_type_imports(spec.imports, resource.update.fields)

    def contribute_route(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit the PATCH /{pk} handler."""
        spec.imports.add_from("sqlalchemy", "update")
        spec.imports.add_from(ctx.model_module, ctx.model.pascal)
        spec.imports.add_from(
            spec.context["utils_module"],
            "assert_rowcount",
        )
        handler = render_snippet(
            "fastapi/ops/update.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            pk_py_type=ctx.pk_py_type,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "update"),
            has_schema=isinstance(resource.update, FieldsConfig),
            response_schema=ctx.response_schema,
        )
        spec.context["route_handlers"].append(handler)


class DeleteOperation:
    """DELETE /{pk} — delete a resource."""

    name = "delete"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when delete is enabled."""
        return resource.delete

    def contribute_schema(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """No-op — delete has no schema."""

    def contribute_route(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit the DELETE /{pk} handler."""
        spec.imports.add_from("sqlalchemy", "delete")
        spec.imports.add_from(ctx.model_module, ctx.model.pascal)
        spec.imports.add_from(
            spec.context["utils_module"],
            "assert_rowcount",
        )
        handler = render_snippet(
            "fastapi/ops/delete.py.j2",
            model_name=ctx.model.pascal,
            model_lower=ctx.model.lower,
            pk_name=ctx.pk_name,
            pk_py_type=ctx.pk_py_type,
            get_db_fn=ctx.get_db_fn,
            has_auth=ctx.has_auth,
            requires_auth=_op_requires_auth(resource, "delete"),
        )
        spec.context["route_handlers"].append(handler)


class ActionOperation:
    """POST /{pk}/{action_slug} — custom action endpoints."""

    name = "actions"

    def enabled(self, resource: ResourceConfig) -> bool:
        """Return True when the resource has actions."""
        return bool(resource.actions)

    def contribute_schema(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit per-action request classes and ActionResponse."""
        for action in resource.actions:
            action_name = Name(action.name)
            if action.params:
                fields = _field_dicts(action.params)
                snippet = render_snippet(
                    "fastapi/schema_parts/action_request.py.j2",
                    request_class=action_name.suffixed("Request"),
                    route_prefix=ctx.route_prefix,
                    slug=action_name.slug,
                    params=fields,
                )
                spec.context["schema_classes"].append(snippet)
                spec.exports.append(action_name.suffixed("Request"))
                _add_field_type_imports(spec.imports, action.params)

        snippet = render_snippet(
            "fastapi/schema_parts/action_response.py.j2",
        )
        spec.context["schema_classes"].append(snippet)
        spec.exports.append("ActionResponse")

    def contribute_route(
        self,
        spec: FileSpec,
        resource: ResourceConfig,
        ctx: SharedContext,
    ) -> None:
        """Emit one handler per action with grouped imports."""
        for action in resource.actions:
            action_name = Name(action.name)
            fn_module, fn_name = Name.from_dotted(action.fn)
            spec.imports.add_from(fn_module, fn_name.raw)

            action_ctx = {
                "name": action_name.raw,
                "fn_name": fn_name.raw,
                "slug": action_name.slug,
                "handler_name": f"{action_name.raw}_action",
                "request_class": action_name.suffixed("Request"),
                "params": _field_dicts(action.params),
                "requires_auth": action.require_auth,
            }
            handler = render_snippet(
                "fastapi/ops/action.py.j2",
                action=action_ctx,
                model_name=ctx.model.pascal,
                pk_name=ctx.pk_name,
                pk_py_type=ctx.pk_py_type,
                get_db_fn=ctx.get_db_fn,
                has_auth=ctx.has_auth,
            )
            spec.context["route_handlers"].append(handler)


def default_operations() -> list[Operation]:
    """Return the default list of built-in CRUD operations.

    Extensions can append to or modify this list::

        from kiln.generators.fastapi.operations import (
            default_operations,
        )

        ops = default_operations()
        ops.append(MyCustomOperation())

    Returns:
        Ordered list of operation instances.

    """
    return [
        GetOperation(),
        ListOperation(),
        CreateOperation(),
        UpdateOperation(),
        DeleteOperation(),
        ActionOperation(),
    ]
