"""Pydantic models for kiln configuration."""

from __future__ import annotations

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


class FieldSpec(BaseModel):
    """A named, typed field — used in operation schemas and action params."""

    name: str
    type: FieldType


class FieldsConfig(BaseModel):
    """An explicit set of fields to expose for a single operation."""

    fields: list[FieldSpec]


class ActionConfig(BaseModel):
    """An action endpoint that calls a Python callable with pk + params.

    The action is mounted at ``POST /{prefix}/{pk}/{name}``.

    ``fn`` is a dotted import path to an async callable, e.g.
    ``"myapp.actions.publish_article"``.  The callable receives the
    primary-key value as its first positional argument and the ``db``
    session as a keyword argument, followed by any declared ``params``.

    The callable is responsible for all database interaction, including
    any SQL function calls.  kiln generates only the FastAPI endpoint
    that wires up the arguments and delegates to the callable.
    """

    name: str
    fn: str
    params: list[FieldSpec] = []
    require_auth: bool = True


CrudOp = Literal["get", "list", "create", "update", "delete"]


class ResourceConfig(BaseModel):
    """A resource: a consumer-defined Python model plus its route configuration.

    ``model`` is a dotted import path to any SQLAlchemy selectable class
    (table, mapped view, etc.) defined by the consumer, e.g.
    ``"myapp.models.Article"``.

    Each CRUD operation is either ``False`` (disabled), ``True`` (all
    columns, schema built at import time via SQLAlchemy inspection), or a
    :class:`FieldsConfig` with an explicit list of fields.

    ``require_auth`` controls which operations require authentication:

    * ``True`` — all enabled operations require auth.
    * ``False`` — no operations require auth.
    * A list of operation names, e.g. ``["create", "update", "delete"]``.
    """

    model: str
    """Dotted import path to the consumer's SQLAlchemy model class,
    e.g. ``"myapp.models.Article"``."""
    pk: str = "id"
    """Primary-key attribute name on the model."""
    pk_type: FieldType = "uuid"
    """Type of the primary key, used to generate the correct path parameter."""
    route_prefix: str | None = None
    """URL prefix for this resource's router, e.g. ``"/articles"``.
    Defaults to ``"/{model_lower}s"`` (simple lowercase + 's').
    """
    db_key: str | None = None
    require_auth: List[CrudOp] | bool = True  # noqa: UP006
    get: bool | FieldsConfig = False
    list: bool | FieldsConfig = False
    create: bool | FieldsConfig = False
    update: bool | FieldsConfig = False
    delete: bool = False
    # List from typing — 'list' (the field above) shadows the built-in in
    # Pydantic's annotation evaluation localns.
    actions: List[ActionConfig] = []  # noqa: UP006


class KilnConfig(BaseModel):
    """Top-level kiln configuration.

    Can be used as either an app-level config or a project-level config.
    When ``apps`` is non-empty kiln treats the file as a project config
    and runs generation for every listed app.
    """

    version: str = "1"
    module: str = "app"
    package_prefix: str = "_generated"
    """Directory prefix prepended to all generated file paths and Python
    import paths.  Defaults to ``"_generated"`` so generated code lives
    at ``_generated/{module}/`` and is imported as
    ``_generated.{module}.routes.article``.  Set to ``""`` to disable.
    """
    auth: AuthConfig | None = None
    databases: list[DatabaseConfig] = []
    resources: list[ResourceConfig] = []
    apps: list[AppRef] = []


class AppRef(BaseModel):
    """An app config entry inside a project-level config."""

    config: KilnConfig
    prefix: str


KilnConfig.model_rebuild()
