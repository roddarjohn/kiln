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

PYTHON_TYPES: dict[FieldType, str] = {
    "uuid": "uuid.UUID",
    "str": "str",
    "email": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "datetime": "datetime",
    "date": "date",
    "json": "dict[str, Any]",
}
"""Python annotation strings for each :data:`FieldType` value.

Used by op builders to render pk/field type annotations into the
generated Pydantic schemas and route handlers.
"""


class AuthConfig(BaseModel):
    """Authentication configuration.

    kiln does not scaffold an auth module — the consumer owns that.
    This config points kiln at three consumer-provided symbols and
    tells it how to shape the generated ``POST {token_url}`` endpoint.

    The consumer provides:

    * :attr:`credentials_schema` -- a Pydantic model (or a
      discriminated union via ``Annotated[A | B,
      Field(discriminator="type")]``) used as the JSON request body
      of the login endpoint.  This is NOT restricted to
      username/password; it can describe API keys, magic-link
      tokens, OAuth codes, or anything else.
    * :attr:`validate_fn` -- called with the parsed schema instance.
      Returns a ``dict`` of session data (encoded into the token) on
      success, or ``None`` to reject with HTTP 401.  The dict can
      carry any JSON-serializable fields the consumer wants on the
      session.
    * :attr:`get_session_fn` -- a FastAPI dependency that validates
      the incoming token/cookie and returns the session dict.
      Every protected route gets ``Depends(...)`` on this.  A thin
      implementation using :mod:`ingot.auth` looks like::

          # myapp/auth.py
          from ingot.auth import bearer_auth
          get_session = bearer_auth(
              token_url="/auth/token",
              secret_env="JWT_SECRET",
              algorithm="HS256",
          )

    Token-endpoint transport:

    * ``type: "jwt"`` -- the endpoint returns an OAuth2-shaped JSON
      body (``{"access_token": ..., "token_type": "bearer"}``).
    * ``type: "cookie"`` -- the endpoint sets an ``httpOnly`` cookie
      named :attr:`cookie_name` and a ``POST {token_url}/logout``
      route clears it.

    Note:
        OAuth2 password-flow form bodies (as used by Swagger's
        *Authorize* button) are not supported yet — the login
        endpoint always accepts JSON.  Consumers who need the
        password-grant form can mount their own route alongside
        and still use :mod:`ingot.auth` for token issuance.

    """

    credentials_schema: str
    """Dotted path to the Pydantic model (or discriminated-union
    type alias) accepted as the JSON request body of the login
    endpoint, e.g. ``"myapp.auth.LoginCredentials"``."""

    validate_fn: str
    """Dotted path to a function ``(creds) -> dict | None`` where
    ``creds`` is the parsed :attr:`credentials_schema` instance.
    Returns the session dict on success or ``None`` to reject."""

    get_session_fn: str
    """Dotted path to the FastAPI dependency that validates the
    incoming token or cookie and returns the session dict,
    e.g. ``"myapp.auth.get_session"``."""

    type: Literal["jwt", "cookie"] = "jwt"
    secret_env: str = "JWT_SECRET"  # noqa: S105
    algorithm: str = "HS256"
    token_url: str = "/auth/token"  # noqa: S105
    cookie_name: str = "access_token"
    """Name of the cookie carrying the JWT when ``type == "cookie"``."""
    cookie_secure: bool = True
    """When ``True`` (default), the cookie is only sent over HTTPS.
    Set to ``False`` for local HTTP development."""
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    """SameSite attribute applied to the auth cookie.  ``"none"``
    requires ``cookie_secure=True`` per RFC 6265bis."""

    @model_validator(mode="after")
    def _samesite_none_requires_secure(self) -> AuthConfig:
        if (
            self.type == "cookie"
            and self.cookie_samesite == "none"
            and not self.cookie_secure
        ):
            msg = (
                "cookie_samesite='none' requires cookie_secure=True "
                "(browsers reject non-Secure SameSite=None cookies)"
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


class ModifierConfig(BaseModel):
    """Configuration for an op modifier.

    Modifiers nest inside their parent op's config (under
    ``modifiers: [...]``) and augment the parent's outputs.  The
    ``type`` field discriminates which modifier op consumes the
    entry — ``"filter"`` routes to :class:`~kiln.operations.filter.Filter`,
    ``"order"`` to :class:`~kiln.operations.order.Order`, etc.  All
    other keys are collected into :attr:`options` via Pydantic's
    ``extra="allow"`` and fed to the modifier op's own ``Options``
    model.

    Same shape as :class:`OperationConfig` — deliberately, so the
    engine treats modifier-scope entries the same way it treats
    operation-scope entries.
    """

    model_config = ConfigDict(extra="allow")

    type: str

    @property
    def options(self) -> dict[str, Any]:
        """Modifier-specific options (all extra fields)."""
        return self.model_extra or {}


class OperationConfig(BaseModel):
    """Configuration for a single operation.

    Known fields (``name``, ``require_auth``) are parsed normally.
    All other keys are collected into :attr:`options` via Pydantic's
    ``extra="allow"`` setting and passed to the operation's
    ``Options`` model (see
    :func:`foundry.operation.operation`).

    Each ``OperationConfig`` is a scope instance of the
    ``"operation"`` scope: the engine descends into
    :attr:`ResourceConfig.operations` and visits each entry
    independently.  Every ``@operation`` class with
    ``dispatch_on="name"`` matches at most one entry per
    resource — the one whose :attr:`name` equals the op's own.

    Examples::

        # Built-in operation
        {"name": "get"}

        # With extra options (go into ``options`` via model_extra)
        {"name": "create", "fields": [...]}

        # Action operation
        {"name": "publish", "fn": "blog.actions.publish", "params": [...]}

        # Custom third-party operation
        {"name": "bulk_create", "class": "my_pkg.ops.BulkOp", "max": 100}
    """

    model_config = ConfigDict(extra="allow")

    name: str
    type: str | None = None
    """Discriminator for non-name-based op dispatch.  CRUD ops
    dispatch on :attr:`name`; ops whose name is user-defined
    (like actions) set ``type`` so the engine can route to the
    right op class.  ``None`` means name-based dispatch."""
    require_auth: bool | None = None
    """Per-operation auth override.  When ``None``, inherits the
    resource-level ``require_auth`` default."""
    modifiers: Annotated[list[ModifierConfig], Scoped(name="modifier")] = Field(
        default_factory=list
    )
    """Modifier entries that nest inside this op and augment its
    outputs.  Today only the list op consumes modifiers (Filter /
    Order / Paginate); every other op leaves this empty."""

    @property
    def options(self) -> dict[str, Any]:
        """Operation-specific options (all extra fields)."""
        return self.model_extra or {}


class ResourceConfig(BaseModel):
    """A resource: a consumer-defined Python model plus its operations.

    ``model`` is a dotted import path to any SQLAlchemy selectable class
    (table, mapped view, etc.) defined by the consumer, e.g.
    ``"myapp.models.Article"``.

    ``operations`` is a scoped list of :class:`OperationConfig`
    entries — each entry becomes an ``"operation"`` scope instance
    that the engine visits independently.

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

    operations: Annotated[list[OperationConfig], Scoped(name="operation")] = (
        Field(default_factory=list)
    )
    """Ordered list of operations to run — each becomes a scope
    instance of ``"operation"`` that the engine visits in turn."""

    generate_tests: bool = False
    """When ``True``, emit a pytest test file for this resource's
    generated routes and serializers."""


class AppConfig(BaseModel):
    """One app within a project: a module of related resources.

    An app owns its own Python package (``module``) and a list of
    resources.
    """

    module: str = "app"
    resources: Annotated[list[ResourceConfig], Scoped(name="resource")] = Field(
        default_factory=list
    )


class App(BaseModel):
    """An app mounted at a URL prefix in the project router."""

    config: AppConfig
    prefix: str = ""


class ProjectConfig(FoundryConfig):
    """Top-level kiln configuration.

    A project is a collection of apps plus shared infrastructure
    (auth, databases, framework target).  Resources always live
    under ``apps[*].config.resources``; the scope tree
    (``project → app → resource``) is the only supported shape.

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
            default = next((db for db in self.databases if db.default), None)

            if not default:
                msg = (
                    "No database has default=True. "
                    "Set default: true on one database "
                    "or specify db_key."
                )

                raise ValueError(msg)

            return default

        matched = next((db for db in self.databases if db.key == db_key), None)

        if not matched:
            msg = f"No database with key '{db_key}' found in databases config."
            raise ValueError(msg)

        return matched


# -------------------------------------------------------------------
# List-extension option shapes.  These are read by the Filter / Order
# / Paginate ops, which run at operation scope with ``type: "filter"``
# / ``type: "order"`` / ``type: "paginate"`` and mutate the List op's
# SearchRequest schema and search RouteHandler.
# -------------------------------------------------------------------


class FilterConfig(BaseModel):
    """Configuration for list filtering.

    When ``fields`` is empty or omitted, all of the list op's
    ``fields`` become filterable; otherwise only the named fields
    are filterable.
    """

    fields: list[str] | None = None


class OrderConfig(BaseModel):
    """Configuration for list ordering."""

    fields: list[str]
    default: str | None = None
    default_dir: Literal["asc", "desc"] = "asc"


class PaginateConfig(BaseModel):
    """Configuration for list pagination."""

    mode: Literal["keyset", "offset"] = "keyset"
    cursor_field: str = "id"
    cursor_type: FieldType = "uuid"
    max_page_size: int = 100
    default_page_size: int = 20
