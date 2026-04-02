"""Pydantic models for kiln configuration."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel

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
    secret_env: str = "JWT_SECRET"
    algorithm: str = "HS256"
    token_url: str = "/auth/token"
    exclude_paths: list[str] = [  # noqa: RUF012
        "/docs",
        "/openapi.json",
        "/health",
    ]


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
    require_auth: list[CrudOp] = []  # noqa: RUF012


class ModelConfig(BaseModel):
    """A pgcraft declarative model definition."""

    name: str
    table: str
    schema: str = "public"
    pgcraft_type: Literal[
        "simple", "append_only", "ledger", "eav"
    ] = "simple"
    pgcraft_plugins: list[str] = []  # noqa: RUF012
    fields: list[FieldConfig]
    crud: CrudConfig | None = None


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

    When ``parameters`` is empty, a pgcraft view stub is generated
    using ``PGCraftViewMixin``. When non-empty, a set-returning
    function stub using ``PGCraftFunctionMixin`` is generated.
    In both cases the FastAPI route queries the named database
    object without embedding SQL logic.
    """

    name: str
    model: str
    description: str = ""
    schema: str = "public"
    parameters: list[ViewParam] = []  # noqa: RUF012
    returns: list[ViewColumn]
    require_auth: bool = True
    http_method: Literal["GET", "POST"] = "GET"


class KilnConfig(BaseModel):
    """Top-level kiln configuration."""

    version: str = "1"
    module: str = "app"
    auth: AuthConfig | None = None
    models: list[ModelConfig] = []  # noqa: RUF012
    views: list[ViewModel] = []  # noqa: RUF012
