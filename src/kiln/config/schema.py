"""Pydantic models for kiln configuration."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

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
    verify_credentials_fn: str | None = None
    """Dotted import path to a credential-verification function,
    e.g. ``"myapp.auth.verify_credentials"``.  The function must
    accept ``(username: str, password: str)`` and return a ``dict``
    (the JWT payload) on success or ``None`` on failure.

    Required when using the default JWT auth flow
    (``get_current_user_fn`` is not set).
    """

    @model_validator(mode="after")
    def _require_verify_credentials(self) -> AuthConfig:
        if (
            self.get_current_user_fn is None
            and self.verify_credentials_fn is None
        ):
            msg = (
                "verify_credentials_fn is required when using "
                "the default JWT auth flow "
                "(get_current_user_fn is not set)"
            )
            raise ValueError(msg)
        return self


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


class OperationConfig(BaseModel):
    """Configuration for a single operation.

    Known fields (``name``, ``require_auth``) are parsed normally.
    All other keys are collected into :attr:`options` via Pydantic's
    ``extra="allow"`` setting and passed to the operation's
    ``Options`` model (see
    :func:`foundry.operation.operation`).

    Examples::

        # String shorthand (expanded to OperationConfig by the pipeline)
        "get"

        # With explicit fields
        {"name": "create", "fields": [...]}

        # Action operation
        {"name": "publish", "fn": "blog.actions.publish", "params": [...]}

        # Custom third-party operation
        {"name": "bulk_create", "class": "my_pkg.ops.BulkOp", "max": 100}
    """

    model_config = ConfigDict(extra="allow")

    name: str
    require_auth: bool | None = None
    """Per-operation auth override.  When ``None``, inherits the
    resource-level ``require_auth`` default."""

    @property
    def options(self) -> dict[str, Any]:
        """Operation-specific options (all extra fields)."""
        return self.model_extra or {}


class ResourceConfig(BaseModel):
    """A resource: a consumer-defined Python model plus its operations.

    ``model`` is a dotted import path to any SQLAlchemy selectable class
    (table, mapped view, etc.) defined by the consumer, e.g.
    ``"myapp.models.Article"``.

    ``operations`` lists the operations to run for this resource.
    Each entry is either a string (built-in operation name resolved
    via entry points) or an :class:`OperationConfig` object.  When
    ``None``, operations are inherited from the parent
    :class:`KilnConfig`.

    ``require_auth`` sets the default authentication requirement for
    all operations.  Individual operations can override this via
    their own ``require_auth`` field.
    """

    model: str
    """Dotted import path to the consumer's SQLAlchemy model class,
    e.g. ``"myapp.models.Article"``."""
    pk: str = "id"
    """Primary-key attribute name on the model."""
    pk_type: FieldType = "uuid"
    """Type of the primary key, used to generate the correct path
    parameter."""
    route_prefix: str | None = None
    """URL prefix for this resource's router, e.g. ``"/articles"``.
    Defaults to ``"/{model_lower}s"`` (simple lowercase + 's').
    """
    db_key: str | None = None
    require_auth: bool = True
    """Default authentication requirement for all operations on this
    resource.  Individual operations can override via their own
    ``require_auth`` field."""
    operations: list[str | OperationConfig] | None = None
    """Ordered list of operations to run.  ``None`` inherits from
    the parent :class:`KilnConfig`."""
    generate_tests: bool = False
    """When ``True``, emit a pytest test file for this resource's
    generated routes and serializers."""


class KilnConfig(BaseModel):
    """Top-level kiln configuration."""

    version: str = "1"
    module: str = "app"
    framework: str = "fastapi"
    """Target framework profile.  Selects which renderer set runs;
    each renderer is tagged with the framework it implements, and
    only those matching this value are used."""
    package_prefix: str = "_generated"
    """Directory prefix prepended to all generated file paths and Python
    import paths.  Defaults to ``"_generated"`` so generated code lives
    at ``_generated/{module}/`` and is imported as
    ``_generated.{module}.routes.article``.  Set to ``""`` to disable.
    """
    auth: AuthConfig | None = None
    databases: list[DatabaseConfig] = []
    operations: list[str | OperationConfig] | None = None
    """Default operations for all resources in this config.  Resources
    can override with their own ``operations`` list."""
    resources: list[ResourceConfig] = []
    apps: list[AppRef] = []


class AppRef(BaseModel):
    """An app config entry inside a project-level config."""

    config: KilnConfig
    prefix: str

    @property
    def module(self) -> str:
        """Expose the app's module name to the engine.

        :func:`foundry.engine._instance_id` derives a scope
        instance's ID from its ``module`` attribute before falling
        back to a positional name.  Surfacing the nested module
        here keeps app-scope store keys stable across reorderings.
        """
        return self.config.module


# KilnConfig.apps references AppRef, which is defined after KilnConfig.
# Pydantic cannot resolve that forward reference during class creation,
# so we force a rebuild once AppRef is available.
KilnConfig.model_rebuild()


def normalize_config(config: KilnConfig) -> KilnConfig:
    """Wrap bare top-level resources in an implicit single app.

    Ensures the scope tree is always ``project → app → resource``
    by converting a config like::

        KilnConfig(module="blog", resources=[...])

    into::

        KilnConfig(module="blog", apps=[AppRef(
            config=KilnConfig(module="blog", resources=[...]),
            prefix="",
        )])

    Configs that already have ``apps`` (or have no resources at
    all) are returned unchanged.

    Args:
        config: Project config, possibly with top-level resources.

    Returns:
        A config whose resources all live under ``apps``.

    """
    if config.apps or not config.resources:
        return config

    inner = KilnConfig(
        module=config.module,
        resources=config.resources,
        operations=config.operations,
    )
    return config.model_copy(
        update={
            "resources": [],
            "apps": [AppRef(config=inner, prefix="")],
        },
    )
