"""Pydantic models for kiln configuration."""

import warnings
from typing import Annotated, Any, List, Literal  # noqa: UP035

from pydantic import BaseModel, Field, field_validator

# Pydantic v2 warns when a field name shadows a deprecated attribute on
# BaseModel.  ``schema`` shadows the deprecated v1-compat ``BaseModel.schema()``
# classmethod, which is intentional — ``schema`` is a valid database concept.
warnings.filterwarnings(
    "ignore",
    message='Field name "schema" .* shadows',
    category=UserWarning,
    module=__name__,
)

FieldType = Literal[
    "uuid",
    "str",
    "email",
    "int",
    "float",
    "bool",
    "datetime",
    "date",
    "json",
]

CrudOp = Literal["create", "read", "update", "delete", "list"]


class AuthConfig(BaseModel):
    """JWT authentication configuration."""

    type: Literal["jwt"] = "jwt"
    secret_env: str = "JWT_SECRET"  # noqa: S105
    algorithm: str = "HS256"
    token_url: str = "/auth/token"  # noqa: S105
    exclude_paths: list[str] = [
        "/docs",
        "/openapi.json",
        "/health",
    ]
    get_current_user_fn: str | None = None
    """Dotted import path to a custom ``get_current_user`` dependency,
    e.g. ``"myapp.auth.custom.get_current_user"``.  When set, the
    generated ``auth/dependencies.py`` re-exports this function instead
    of containing the default JWT implementation.
    """


# SQLAlchemy / pgcraft attribute names that must not be used as column names.
# Using these would shadow class-level attributes that the ORM relies on.
_RESERVED_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "metadata",  # SQLAlchemy MetaData object on the base class
        "table",  # pgcraft sets cls.table after pipeline runs
        "ctx",  # pgcraft sets cls.ctx after pipeline runs
        "query",  # SQLAlchemy legacy Session.query shim
        "registry",  # pgcraft internal
    }
)


class FieldConfig(BaseModel):
    """A single field on a pgcraft model."""

    name: str
    type: FieldType
    primary_key: bool | str = False
    """``False`` — not a primary key.  ``True`` — PK using the default plugin
    for this field type.  A dotted import path string (e.g.
    ``"pgcraft.plugins.pk.UUIDV7PKPlugin"``) — PK using that specific plugin.
    """
    unique: bool = False
    nullable: bool = False
    foreign_key: str | None = None
    exclude_from_api: bool = False
    auto_now_add: bool = False
    auto_now: bool = False
    index: bool = False

    @field_validator("name")
    @classmethod
    def name_not_reserved(cls, v: str) -> str:
        """Reject field names that clash with SQLAlchemy/pgcraft internals."""
        if v in _RESERVED_COLUMN_NAMES:
            msg = (
                f"Field name '{v}' is reserved by SQLAlchemy/pgcraft and "
                f"cannot be used as a column attribute name. "
                f"Choose a different name (e.g. '{v}_data' or 'extra_{v}')."
            )
            raise ValueError(msg)
        return v


class CrudConfig(BaseModel):
    """CRUD operation settings for a model."""

    create: bool = True
    read: bool = True
    update: bool = True
    delete: bool = True
    list: bool = True
    paginated: bool = True
    require_auth: List[CrudOp] = []  # noqa: UP006


class DatabaseConfig(BaseModel):
    """Configuration for a single database connection."""

    key: str
    url_env: str = "DATABASE_URL"
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = -1
    pool_pre_ping: bool = True
    default: bool = False


class PluginRef(BaseModel):
    """A pgcraft plugin with constructor arguments.

    Used in ``pgcraft_plugins`` when the plugin requires configuration.
    ``path`` is a dotted import path; ``args`` is a mapping of keyword
    arguments passed verbatim to the plugin constructor.

    Example::

        { "path": "pgcraft.extensions.postgrest.plugin.PostgRESTPlugin",
          "args": { "grants": ["select", "insert"] } }

    """

    path: str
    args: dict[str, Any] = {}


class ModelConfig(BaseModel):
    """A pgcraft declarative model definition."""

    name: str
    table: str
    schema: str = "public"
    pgcraft_type: str = "pgcraft.factory.dimension.simple.PGCraftSimple"
    """Dotted import path to the pgcraft factory class, e.g.
    ``"pgcraft.factory.dimension.simple.PGCraftSimple"``.  The stdlib
    ``kiln/pgcraft/factories.libsonnet`` provides named aliases.
    """
    pgcraft_plugins: list[str | PluginRef] = []
    """Additional pgcraft plugins to include in ``__pgcraft__``.

    Each entry is either a dotted import path string (for no-arg plugins)
    or a :class:`PluginRef` object with ``path`` and ``args`` (for plugins
    that require constructor arguments).  The stdlib
    ``kiln/pgcraft/plugins.libsonnet`` provides named helpers for both forms.
    """
    fields: list[FieldConfig]


class ViewParam(BaseModel):
    """An input parameter for a parameterized view or function."""

    name: str
    type: FieldType


class ViewColumn(BaseModel):
    """An output column from a view or set-returning function."""

    name: str
    type: FieldType


class ViewConfig(BaseModel):
    """A database view or set-returning function."""

    name: str
    schema: str = "public"
    parameters: list[ViewParam] = []
    returns: list[ViewColumn]
    db_key: str | None = None


# ---------------------------------------------------------------------------
# Route config classes
# ---------------------------------------------------------------------------


class CRUDRouteConfig(BaseModel):
    """CRUD routes for a model."""

    type: Literal["crud"] = "crud"
    model: str
    crud: CrudConfig
    db_key: str | None = None


class ViewRouteConfig(BaseModel):
    """Route exposing a database view or set-returning function."""

    type: Literal["view"] = "view"
    view: str
    http_method: Literal["GET", "POST"] = "GET"
    require_auth: bool = True
    description: str = ""
    db_key: str | None = None


class ActionRouteConfig(BaseModel):
    """Mutation route (POST) calling a database function."""

    type: Literal["action"] = "action"
    name: str
    fn: str  # "schema.function_name"
    params: list[ViewParam] = []
    returns: list[ViewColumn] = []
    require_auth: bool = True
    description: str = ""
    db_key: str | None = None


RouteConfig = Annotated[
    CRUDRouteConfig | ViewRouteConfig | ActionRouteConfig,
    Field(discriminator="type"),
]


class KilnConfig(BaseModel):
    """Top-level kiln configuration.

    Can be used as either an app-level config (``module``, ``models``,
    ``views``) or a project-level config (``apps``, ``auth``,
    ``databases``).  When ``apps`` is non-empty kiln treats the file as
    a project config and runs generation for every listed app.
    """

    version: str = "1"
    module: str = "app"
    auth: AuthConfig | None = None
    databases: list[DatabaseConfig] = []
    models: list[ModelConfig] = []
    views: list[ViewConfig] = []
    routes: list[RouteConfig] = []
    apps: list["AppRef"] = []


class AppRef(BaseModel):
    """An app config entry inside a project-level config."""

    config: KilnConfig
    prefix: str


KilnConfig.model_rebuild()
