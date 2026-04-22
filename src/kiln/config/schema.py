"""Pydantic models for kiln configuration."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foundry.config import FoundryConfig
from foundry.scope import Scoped

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

    @property
    def session_module(self) -> str:
        """Dotted module path of the scaffolded session file.

        Matches what :class:`DbScaffold` emits at
        ``db/{key}_session.py``.
        """
        return f"db.{self.key}_session"

    @property
    def get_db_fn(self) -> str:
        """Name of the FastAPI dependency exposed by the session module."""
        return f"get_{self.key}_db"


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
    :class:`AppConfig`.

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
    the parent :class:`AppConfig`."""
    generate_tests: bool = False
    """When ``True``, emit a pytest test file for this resource's
    generated routes and serializers."""


class AppConfig(BaseModel):
    """One app within a project: a module of related resources.

    An app owns its own Python package (``module``) and a list of
    resources.  ``operations`` is the default operation set inherited
    by resources that don't declare their own.
    """

    module: str = "app"
    operations: list[str | OperationConfig] | None = None
    """Default operations for all resources in this app.  Resources
    can override with their own ``operations`` list."""
    resources: Annotated[list[ResourceConfig], Scoped(name="resource")] = Field(
        default_factory=list
    )


class App(BaseModel):
    """An app mounted at a URL prefix in the project router."""

    config: AppConfig
    prefix: str = ""

    @property
    def module(self) -> str:
        """Expose the app's module name to the engine.

        :func:`foundry.engine._instance_id` derives a scope
        instance's ID from its ``module`` attribute before falling
        back to a positional name.  Surfacing the nested module
        here keeps app-scope store keys stable across reorderings.
        """
        return self.config.module


class ProjectConfig(FoundryConfig):
    """Top-level kiln configuration.

    A project is a collection of apps plus shared infrastructure
    (auth, databases, framework target).  Resources always live
    under ``apps[*].config.resources``; a shorthand config with
    top-level ``module`` / ``resources`` / ``operations`` fields is
    wrapped into a single implicit app with ``prefix=""`` by
    :meth:`_wrap_shorthand`, so the scope tree
    (``project → app → resource``) is uniform across configs.

    Inherits :attr:`~foundry.config.FoundryConfig.package_prefix`
    from foundry and overrides its default to ``"_generated"`` so
    generated code lives at ``_generated/{module}/`` and is
    imported as ``_generated.{module}.routes.article``.  Set it to
    ``""`` to disable the prefix.
    """

    version: str = "1"
    framework: str = "fastapi"
    """Target framework profile.  Selects which renderer set runs;
    each renderer is tagged with the framework it implements, and
    only those matching this value are used."""
    package_prefix: str = "_generated"
    auth: AuthConfig | None = None
    databases: list[DatabaseConfig] = Field(..., min_length=1)
    apps: Annotated[list[App], Scoped(name="app")] = Field(
        default_factory=list,
    )

    @model_validator(mode="before")
    @classmethod
    def _wrap_shorthand(cls, data: Any) -> Any:  # type: ignore[operator]  # noqa: ANN401
        """Wrap single-app shorthand into an implicit ``apps`` entry.

        A config like ``{"module": "blog", "resources": [...]}`` is
        rewritten to
        ``{"apps": [{"config": {"module": "blog", "resources": [...]},
        "prefix": ""}]}`` so the scope tree always runs
        ``project → app → resource``.  Configs that already set
        ``apps`` are returned unchanged.
        """
        if not isinstance(data, dict) or "apps" in data:
            return data
        app_keys = ("module", "resources", "operations")
        if not any(k in data for k in app_keys):
            return data
        app_data = {k: data.pop(k) for k in list(data.keys()) if k in app_keys}
        data["apps"] = [{"config": app_data, "prefix": ""}]
        return data

    def resolve_database(self, db_key: str | None) -> DatabaseConfig:
        """Return the :class:`DatabaseConfig` selected by *db_key*.

        When *db_key* is ``None``, returns the database marked
        ``default=True``.

        Raises:
            ValueError: If *db_key* does not match any configured
                database, or if no database has ``default=True`` and
                *db_key* is ``None``.

        """
        if db_key is None:
            defaults = [d for d in self.databases if d.default]
            if not defaults:
                msg = (
                    "No database has default=True. "
                    "Set default: true on one database "
                    "or specify db_key."
                )
                raise ValueError(msg)
            return defaults[0]
        matches = [d for d in self.databases if d.key == db_key]
        if not matches:
            msg = f"No database with key '{db_key}' found in databases config."
            raise ValueError(msg)
        return matches[0]
