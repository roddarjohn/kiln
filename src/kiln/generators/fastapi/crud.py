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
    from kiln.config.schema import (
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
        """Return True when any model has a crud config.

        Args:
            config: The validated kiln configuration.

        """
        return any(m.crud is not None for m in config.models)

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Generate one route file per model that has a crud config.

        Args:
            config: The validated kiln configuration.

        Returns:
            One :class:`~kiln.generators.base.GeneratedFile` per
            qualifying model, written to
            ``api/routes/<name_lower>.py``.

        """
        has_auth = config.auth is not None
        files: list[GeneratedFile] = [
            GeneratedFile(path="schemas/__init__.py", content=""),
        ]
        for m in config.models:
            if m.crud is None:
                continue
            session_module, get_db_fn = resolve_db_session(
                m.db_key, config.databases
            )
            files.append(
                GeneratedFile(
                    path=f"schemas/{m.name.lower()}.py",
                    content=_render_schemas(m),
                )
            )
            files.append(
                GeneratedFile(
                    path=f"routes/{m.name.lower()}.py",
                    content=_render_crud(
                        m,
                        config.module,
                        has_auth=has_auth,
                        session_module=session_module,
                        get_db_fn=get_db_fn,
                    ),
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
    module: str,
    *,
    has_auth: bool,
    session_module: str,
    get_db_fn: str,
) -> str:
    """Render the CRUD route file for *model*.

    Args:
        model: The model configuration.  ``model.crud`` must not be None.
        module: Root module name for generated import paths.
        has_auth: Whether the project has an auth config.
        session_module: Dotted sub-path to the session module, e.g.
            ``"db.primary_session"``.
        get_db_fn: Name of the dependency function, e.g. ``"get_primary_db"``.

    Returns:
        Python source string.

    """
    crud = model.crud
    if crud is None:
        msg = f"_render_crud called on model '{model.name}' with no crud config"
        raise ValueError(msg)
    pk = _pk(model)
    tmpl = env.get_template("fastapi/crud_routes.py.j2")
    return tmpl.render(
        model=model,
        model_lower=model.name.lower(),
        module=module,
        imports=type_imports([pk.type]),
        has_auth=has_auth,
        pk={"name": pk.name, "py_type": PYTHON_TYPES[pk.type]},
        crud=crud,
        session_module=session_module,
        get_db_fn=get_db_fn,
    )
