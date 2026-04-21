"""FastAPI renderers for build output types.

Each renderer converts a build output object into a code
string using the existing Jinja2 templates.  Register all
renderers into a :class:`~foundry.render.RenderRegistry`
via :func:`create_registry`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.outputs import (
    EnumClass,
    RouteHandler,
    RouterMount,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.render import RenderRegistry
from kiln.generators._env import render_snippet

if TYPE_CHECKING:
    from foundry.render import RenderCtx


def create_registry() -> RenderRegistry:
    """Build a registry with all FastAPI renderers.

    Returns:
        A fully populated :class:`RenderRegistry`.

    """
    reg = RenderRegistry()

    @reg.renders(SchemaClass)
    def render_schema_class(
        schema: SchemaClass,
        _ctx: RenderCtx,
    ) -> str:
        """Render a Pydantic model class."""
        fields = [
            {
                "name": f.name,
                "py_type": f.py_type,
                "optional": f.optional,
            }
            for f in schema.fields
        ]
        return render_snippet(
            "fastapi/schema_parts/schema_class.py.j2",
            class_name=schema.name,
            doc=schema.doc,
            fields=fields,
        )

    @reg.renders(EnumClass)
    def render_enum_class(
        enum: EnumClass,
        _ctx: RenderCtx,
    ) -> str:
        """Render an Enum class definition."""
        lines = [f"class {enum.name}({enum.base}):"]
        for member_name, member_value in enum.members:
            lines.append(f"    {member_name} = {member_value!r}")
        return "\n".join(lines)

    @reg.renders(RouteHandler)
    def render_route_handler(
        handler: RouteHandler,
        _ctx: RenderCtx,
    ) -> str:
        """Render a route handler function."""
        return _render_handler_string(handler)

    @reg.renders(RouterMount)
    def render_router_mount(
        mount: RouterMount,
        _ctx: RenderCtx,
    ) -> str:
        """Render a router include statement."""
        prefix = f', prefix="{mount.prefix}"' if mount.prefix else ""
        return (
            f"from {mount.module} import router "
            f"as {mount.alias}\n"
            f"app_router.include_router("
            f"{mount.alias}{prefix})"
        )

    @reg.renders(SerializerFn)
    def render_serializer(
        ser: SerializerFn,
        _ctx: RenderCtx,
    ) -> str:
        """Render a serializer function."""
        fields = [{"name": f.name, "py_type": f.py_type} for f in ser.fields]
        return render_snippet(
            "fastapi/serializer_outer.py.j2",
            model_name=ser.model_name,
            model_lower=ser.model_name.lower(),
            resource_class=ser.schema_name,
            resource_fields=fields,
            import_block="",
        )

    @reg.renders(TestCase)
    def render_test_case(
        tc: TestCase,
        _ctx: RenderCtx,
    ) -> str:
        """Render a test case dict for the test template."""
        # TestCase build output is consumed by the assembler, not
        # rendered to a standalone string.  Return a repr
        # for debugging; the assembler uses the object
        # directly.
        return repr(tc)

    @reg.renders(StaticFile)
    def render_static_file(
        sf: StaticFile,
        ctx: RenderCtx,
    ) -> str:
        """Render a static file from its template."""
        if not sf.template:
            return ""
        tmpl = ctx.env.get_template(sf.template)
        return tmpl.render(**sf.context)

    return reg


def _render_handler_string(handler: RouteHandler) -> str:
    """Build a handler function string from build output fields.

    This produces the same output as the individual ops/*.j2
    templates but directly from the output, so that downstream
    renderers or tests can override behavior by mutating the
    build output before rendering.

    Args:
        handler: The route handler build output object.

    Returns:
        Rendered handler code as a string.

    """
    lines: list[str] = list(handler.decorators)

    # @router.method signature
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

    # Function signature
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

    # Docstring
    if handler.doc:
        lines.append(f'    """{handler.doc}"""')

    # Body
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
