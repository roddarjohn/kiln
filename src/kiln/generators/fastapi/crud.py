"""Generator that produces FastAPI CRUD route files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.config.schema import FieldConfig
from kiln.generators._env import env
from kiln.generators._helpers import (
    PYTHON_TYPES,
    resolve_db_session,
    type_imports,
)
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kiln.config.schema import (
        CRUDRouteConfig,
        KilnConfig,
        ModelConfig,
    )

_PK_DEFAULT = FieldConfig(name="id", type="int", primary_key=True)


class CRUDGenerator:
    """Produces a FastAPI router file with CRUD endpoints per model.

    Each generated file contains Pydantic request/response schemas
    and async route handlers that use SQLAlchemy's ORM.  Auth
    dependencies are injected only for operations listed in
    ``crud.require_auth``.

    Generated files are always overwritten on re-generation.
    """

    @property
    def name(self) -> str:
        """Unique generator identifier."""
        return "crud"

    def can_generate(self, config: KilnConfig) -> bool:
        """Return True when any route is a CRUDRouteConfig.

        Args:
            config: The validated kiln configuration.

        """
        # Import at runtime to avoid circular imports at module load time
        from kiln.config.schema import CRUDRouteConfig  # noqa: PLC0415

        return any(isinstance(r, CRUDRouteConfig) for r in config.routes)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate one route file per CRUD route config.

        Args:
            config: The validated kiln configuration.

        Returns:
            One :class:`~kiln.generators.base.GeneratedFile` per
            qualifying route, written to
            ``api/routes/<name_lower>.py``.

        """
        from kiln.config.schema import CRUDRouteConfig  # noqa: PLC0415

        has_auth = config.auth is not None
        app = config.module
        model_map = {m.name: m for m in config.models}
        files: list[GeneratedFile] = [
            GeneratedFile(path=f"{app}/schemas/__init__.py", content=""),
        ]
        for route in config.routes:
            if not isinstance(route, CRUDRouteConfig):
                continue
            model = model_map[route.model]
            session_module, get_db_fn = resolve_db_session(
                route.db_key, config.databases
            )
            files.append(
                GeneratedFile(
                    path=f"{app}/schemas/{model.name.lower()}.py",
                    content=_render_schemas(model),
                )
            )
            ctx = {
                "has_auth": has_auth,
                "session_module": session_module,
                "get_db_fn": get_db_fn,
            }
            files.append(
                GeneratedFile(
                    path=f"{app}/routes/{model.name.lower()}.py",
                    content=_render_crud(model, route, config.module, ctx),
                )
            )
        return files


# ---------------------------------------------------------------------------
# Internal rendering helpers
# ---------------------------------------------------------------------------


def _pk(model: ModelConfig) -> FieldConfig:
    """Return the primary-key field, falling back to a synthetic one."""
    return next(
        (f for f in model.fields if f.primary_key),
        _PK_DEFAULT,
    )


def _api_fields(fields: list[FieldConfig]) -> list[FieldConfig]:
    """Fields that appear in the API (not excluded, not internal)."""
    return [f for f in fields if not f.exclude_from_api]


def _create_fields(fields: list[FieldConfig]) -> list[FieldConfig]:
    """Fields that a client supplies when creating a resource."""
    return [
        f
        for f in _api_fields(fields)
        if not f.primary_key and not f.auto_now_add and not f.auto_now
    ]


def _response_fields(fields: list[FieldConfig]) -> list[FieldConfig]:
    """Fields included in the response model."""
    return _api_fields(fields)


def _render_schemas(model: ModelConfig) -> str:
    """Render the Pydantic schema file for *model*.

    Args:
        model: The model configuration.

    Returns:
        Python source string.

    """
    create_flds = _create_fields(model.fields)
    resp_flds = _response_fields(model.fields)
    tmpl = env.get_template("fastapi/schemas.py.j2")
    return tmpl.render(
        model=model,
        imports=type_imports([f.type for f in model.fields]),
        create_fields=[
            {"name": f.name, "py_type": PYTHON_TYPES[f.type]}
            for f in create_flds
        ],
        response_fields=[
            {
                "name": f.name,
                "py_type": PYTHON_TYPES[f.type],
                "nullable": f.nullable,
            }
            for f in resp_flds
        ],
    )


def _render_crud(
    model: ModelConfig,
    route: CRUDRouteConfig,
    module: str,
    ctx: Mapping[str, object],
) -> str:
    """Render the CRUD route file for *model*.

    Args:
        model: The model configuration.
        route: The CRUD route configuration supplying ``crud`` settings.
        module: Root module name for generated import paths.
        ctx: Rendering context with keys ``has_auth``, ``session_module``,
            and ``get_db_fn``.

    Returns:
        Python source string.

    """
    pk = _pk(model)
    tmpl = env.get_template("fastapi/crud_routes.py.j2")
    return tmpl.render(
        model=model,
        model_lower=model.name.lower(),
        module=module,
        imports=type_imports([pk.type]),
        has_auth=ctx["has_auth"],
        pk={"name": pk.name, "py_type": PYTHON_TYPES[pk.type]},
        crud=route.crud,
        session_module=ctx["session_module"],
        get_db_fn=ctx["get_db_fn"],
    )
