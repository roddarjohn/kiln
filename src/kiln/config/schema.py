"""Pydantic models for kiln configuration."""

import warnings
from typing import List, Literal  # noqa: UP035

from pydantic import BaseModel

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


class FieldConfig(BaseModel):
    """A single field on a pgcraft model."""

    name: str
    type: FieldType
    primary_key: bool = False
    unique: bool = False
    nullable: bool = False
    foreign_key: str | None = None
    exclude_from_api: bool = False
    auto_now_add: bool = False
    auto_now: bool = False
    index: bool = False


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


class ModelConfig(BaseModel):
    """A pgcraft declarative model definition."""

    name: str
    table: str
    schema: str = "public"
    pgcraft_type: Literal["simple", "append_only", "ledger", "eav"] = "simple"
    pgcraft_plugins: list[str] = []
    fields: list[FieldConfig]
    crud: CrudConfig | None = None
    db_key: str | None = None


class ViewParam(BaseModel):
    """An input parameter for a parameterized view or function."""

    name: str
    type: FieldType


class ViewColumn(BaseModel):
    """An output column from a view or set-returning function."""

    name: str
    type: FieldType


class ViewModel(BaseModel):
    """A database view or function exposed as a FastAPI endpoint.

    When ``parameters`` is empty the route calls ``query_fn()`` to
    obtain a SQLAlchemy ``select()`` expression — the developer writes
    and owns that function.  When ``parameters`` is non-empty the route
    calls the named set-returning function via
    ``func.<schema>.<name>(params).table_valued(cols)``.
    """

    name: str
    model: str
    description: str = ""
    schema: str = "public"
    parameters: list[ViewParam] = []
    returns: list[ViewColumn]
    require_auth: bool = True
    http_method: Literal["GET", "POST"] = "GET"
    query_fn: str | None = None
    db_key: str | None = None
    """Dotted import path to a zero-argument function returning a
    SQLAlchemy ``select()`` expression, e.g.
    ``"app.db.views.published_articles.get_query"``.
    Required for non-parameterised views; unused for function views.
    """


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
    views: list[ViewModel] = []
    apps: list["AppRef"] = []


class AppRef(BaseModel):
    """An app config entry inside a project-level config."""

    config: KilnConfig
    prefix: str


KilnConfig.model_rebuild()
