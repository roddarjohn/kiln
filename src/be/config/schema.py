"""Pydantic models for be configuration."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foundry.config import FoundryConfig
from foundry.scope import Scoped

NESTED: Literal["nested"] = "nested"
"""Sentinel value for :attr:`FieldSpec.type` that marks a field as a
nested dump of a related model rather than a scalar."""


LoaderStrategy = Literal["selectin", "joined", "subquery"]
"""SQLAlchemy eager-loading strategy for a nested field.  Generated
handlers translate this to the matching ``sqlalchemy.orm`` loader
(``selectinload`` / ``joinedload`` / ``subqueryload``) on the
``select(...)`` statement so the related row is available when the
serializer reads ``obj.{field}``."""


_DEFAULT_LOAD: LoaderStrategy = "selectin"

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
"""Python annotation strings for each :data:`~be.config.schema.FieldType`.

Used by op builders to render pk/field type annotations into the
generated Pydantic schemas and route handlers.
"""


class AuthConfig(BaseModel):
    """Authentication configuration.

    be owns the auth *package* (dependency + login/logout routes);
    the consumer owns the three types that characterise their domain:

    * :attr:`credentials_schema` -- Pydantic model (or discriminated
      union via ``Annotated[A | B, Field(discriminator="type")]``)
      used as the JSON request body of the login endpoint.  Not
      restricted to username/password — can describe API keys,
      magic-link tokens, OAuth codes, whatever.
    * :attr:`session_schema` -- Pydantic model describing what the
      token carries (user id, tenant, roles, ...).  Flows through
      protected routes as ``session: <Schema>``.
    * :attr:`validate_fn` -- ``(creds) -> Session | None``.  The
      consumer's business logic for deciding a login is valid.

    :attr:`sources` controls which transports carry the token:

    * ``["bearer"]`` -- login returns an OAuth2-shaped JSON body;
      ``get_session`` reads the ``Authorization`` header.
    * ``["cookie"]`` -- login sets an ``httpOnly`` cookie;
      ``get_session`` reads it.
    * ``["bearer", "cookie"]`` -- login does both, so the same
      endpoint serves both web and API clients; ``get_session``
      accepts either.

    Note:
        OAuth2 password-flow form bodies (as used by Swagger's
        *Authorize* button) are not supported yet — the login
        endpoint always accepts JSON.

    """

    credentials_schema: str
    """Dotted path to the Pydantic model (or discriminated-union
    type alias) accepted as the JSON request body of the login
    endpoint, e.g. ``"myapp.auth.LoginCredentials"``."""

    session_schema: str
    """Dotted path to the Pydantic model carried in the token,
    e.g. ``"myapp.auth.Session"``.  Fields must be JSON-serializable
    so Pydantic can round-trip the model through the JWT claims."""

    validate_fn: str
    """Dotted path to a function ``(creds) -> Session | None`` where
    ``creds`` is the parsed :attr:`credentials_schema` instance and
    ``Session`` is the :attr:`session_schema` model.  Returns the
    session on success or ``None`` to reject with HTTP 401."""

    sources: list[Literal["bearer", "cookie"]] = Field(
        default_factory=lambda: ["bearer"],
        min_length=1,
    )
    """Ordered list of token transports.  At least one required;
    any subset of ``{"bearer", "cookie"}`` in any order."""

    secret_env: str = "JWT_SECRET"  # noqa: S105
    algorithm: str = "HS256"
    token_url: str = "/auth/token"  # noqa: S105

    session_store: str | None = None
    """Dotted path to an :class:`ingot.auth.SessionStore` instance
    (e.g. ``"myapp.revocation.revocations"``).  When set, the
    generated ``get_session`` enforces the deny-list and logout
    calls :meth:`~ingot.auth.SessionStore.revoke` before clearing;
    ``None`` = stateless."""

    cookie_name: str = "access_token"
    """Name of the cookie carrying the JWT when ``"cookie"`` is in
    :attr:`sources`."""
    cookie_secure: bool = True
    """When ``True`` (default), the cookie is only sent over HTTPS.
    Set to ``False`` for local HTTP development."""
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    """SameSite attribute applied to the auth cookie.  ``"none"``
    requires ``cookie_secure=True`` per RFC 6265bis."""

    @model_validator(mode="after")
    def _sources_unique(self) -> AuthConfig:
        if len(set(self.sources)) != len(self.sources):
            msg = f"sources must not contain duplicates: {self.sources}"
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _samesite_none_requires_secure(self) -> AuthConfig:
        if (
            "cookie" in self.sources
            and self.cookie_samesite == "none"
            and not self.cookie_secure
        ):
            msg = (
                "cookie_samesite='none' requires cookie_secure=True "
                "(browsers reject non-Secure SameSite=None cookies)"
            )
            raise ValueError(msg)

        return self


SamplerName = Literal[
    "always_on",
    "always_off",
    "parentbased_always_on",
    "parentbased_always_off",
    "parentbased_traceidratio",
    "traceidratio",
]
"""OpenTelemetry sampler names accepted by
:class:`TelemetryConfig.sampler`.  ``parentbased_always_on`` (the
default) keeps a parent's sampling decision when one exists and
otherwise samples every trace -- a friendly default for development.
Production users typically switch to ``parentbased_traceidratio``
with a low :attr:`TelemetryConfig.sampler_ratio`."""


ExporterName = Literal["otlp_http", "otlp_grpc", "console", "none"]
"""Exporter selection for :class:`TelemetryConfig.exporter`.
``"none"`` disables span export (useful when only metrics/logs are
needed, or when a sidecar Collector reads from another source).
Leave the field as ``None`` to defer to the standard
``OTEL_EXPORTER_OTLP_*`` environment variables at runtime."""


class TelemetryConfig(BaseModel):
    """OpenTelemetry instrumentation for the generated app.

    Set ``project.telemetry`` to an instance of this class to opt
    in.  When unset, the generator emits zero references to
    OpenTelemetry -- the runtime cost is exactly zero.

    All toggle fields default to *sensible-for-most-projects*
    values: traces and metrics on, logs off (the OTel logs SDK is
    the youngest of the three signal APIs), FastAPI and SQLAlchemy
    auto-instrumented, request/response bodies *not* captured
    (PII risk), and ``parentbased_always_on`` sampling for friendly
    development defaults.

    The auth router (login/logout) always scrubs credentials and
    session payloads from spans regardless of
    :attr:`capture_request_body` / :attr:`capture_response_body`.
    """

    service_name: str
    """Value emitted as the ``service.name`` resource attribute on
    every signal.  Required -- there is no sensible default."""

    service_version: str | None = None
    """Optional ``service.version`` resource attribute."""

    environment_env: str = "ENVIRONMENT"
    """Environment variable name read at startup for the
    ``deployment.environment.name`` resource attribute, e.g.
    ``"prod"`` / ``"staging"`` / ``"dev"``.  Set to a name your
    deployment already exports (default: ``ENVIRONMENT``); leave
    the variable unset at runtime and the attribute is omitted.

    Generated artifacts must be portable across deployments, so
    the *value* is intentionally not config-time -- only the
    variable name is."""

    traces: bool = True
    """Emit trace spans."""
    metrics: bool = True
    """Emit metrics."""
    logs: bool = False
    """Emit logs through the OTel logs SDK.  Off by default because
    the logs SDK API surface is the youngest and most likely to
    churn; enable when your collector pipeline is ready."""

    instrument_fastapi: bool = True
    """Wire FastAPIInstrumentor into the project router so every
    HTTP request becomes a server span."""
    instrument_sqlalchemy: bool = True
    """Wire SQLAlchemyInstrumentor against each generated async
    engine so every query becomes a client span."""
    instrument_httpx: bool = False
    """Wire HTTPXClientInstrumentor.  Off by default -- generated
    apps don't make outbound HTTP themselves; turn on when consumer
    code does."""
    instrument_requests: bool = False
    """Wire RequestsInstrumentor for the ``requests`` library.  Off
    by default for the same reason as :attr:`instrument_httpx` -- the
    consumer opts in when they have ``requests``-based outbound calls
    they want to trace."""
    instrument_logging: bool = False
    """Inject trace/span ids into stdlib log records via
    LoggingInstrumentor.  Off by default to avoid mutating logging
    config the consumer didn't ask for."""

    span_per_handler: bool = True
    """Wrap every generated CRUD handler in an internal span named
    ``{resource}.{op}``.  Complements FastAPI's request span with
    a clean op-scoped boundary that survives middleware reordering
    and includes resource/op as low-cardinality attributes."""
    span_per_action: bool = True
    """Wrap every generated action handler in an internal span."""

    capture_request_body: bool = False
    """Attach a (truncated) request body string as a span attribute.
    Off by default -- request bodies frequently contain PII."""
    capture_response_body: bool = False
    """Attach a (truncated) response body string as a span attribute.
    Off by default -- response bodies frequently contain PII."""

    sampler: SamplerName = "parentbased_always_on"
    """Sampler choice.  ``"parentbased_always_on"`` is friendly for
    dev (sample everything, but honour parent decisions); production
    users typically switch to ``"parentbased_traceidratio"`` and set
    :attr:`sampler_ratio`."""

    sampler_ratio: float | None = None
    """Sampling ratio in ``[0.0, 1.0]``.  Required when
    :attr:`sampler` is ``"traceidratio"`` or
    ``"parentbased_traceidratio"``; rejected otherwise."""

    exporter: ExporterName | None = None
    """Span exporter selection.  ``None`` defers to the standard
    ``OTEL_EXPORTER_OTLP_*`` environment variables at runtime
    (recommended for vendor-neutral deployments).  Set explicitly
    to force a transport regardless of the environment.

    The OTel SDK already reads ``OTEL_EXPORTER_OTLP_ENDPOINT`` and
    ``OTEL_EXPORTER_OTLP_HEADERS`` natively when the exporter is
    constructed -- be does not duplicate that lookup, so override
    the *values* of the standard variables in your deployment
    config; there are no kiln-side knobs for the variable *names*."""

    resource_attributes: dict[str, str] = Field(default_factory=dict)
    """Extra static resource attributes added to every signal,
    e.g. ``{"team": "platform", "tier": "edge"}``."""

    @model_validator(mode="after")
    def _ratio_required_for_ratio_samplers(self) -> TelemetryConfig:
        ratio_samplers = ("traceidratio", "parentbased_traceidratio")

        if self.sampler in ratio_samplers and self.sampler_ratio is None:
            msg = (
                f"sampler={self.sampler!r} requires sampler_ratio "
                f"to be set in [0.0, 1.0]"
            )
            raise ValueError(msg)

        if (
            self.sampler not in ratio_samplers
            and self.sampler_ratio is not None
        ):
            msg = (
                f"sampler_ratio is only valid with a *ratio sampler "
                f"(got sampler={self.sampler!r})"
            )
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _ratio_in_unit_interval(self) -> TelemetryConfig:
        if self.sampler_ratio is None:
            return self

        if not 0.0 <= self.sampler_ratio <= 1.0:
            msg = (
                f"sampler_ratio must be in [0.0, 1.0], got "
                f"{self.sampler_ratio!r}"
            )
            raise ValueError(msg)

        return self


class RateLimitConfig(BaseModel):
    """Project-level rate-limiting config.

    Storage is Postgres-backed via a consumer-defined SQLAlchemy
    model that mixes in :class:`ingot.rate_limit.RateLimitBucketMixin`
    -- the same idiom as :class:`ingot.files.FileMixin` (the consumer
    owns the table, we own the columns).

    Library is `slowapi <https://github.com/laurentS/slowapi>`__ with
    a custom :class:`ingot.rate_limit.PostgresStorage` adapter for the
    underlying ``limits`` library so users don't need Redis.
    """

    bucket_model: str
    """Dotted import path to the consumer's bucket model, e.g.
    ``"myapp.models.RateLimitBucket"``.  Must mix in
    :class:`ingot.rate_limit.RateLimitBucketMixin`."""

    default_limit: str | None = None
    """Optional project-wide fallback applied to any op that doesn't
    have its own ``rate_limit``.  Format follows the ``limits``
    library: ``"100/minute"``, ``"5/second;1000/hour"``, etc.  When
    ``None``, only ops with an explicit ``rate_limit`` are limited."""

    key_func: str | None = None
    """Dotted path to a sync function ``(request: Request) -> str``
    used as the rate-limit key.  When ``None``, defaults to
    :func:`ingot.rate_limit.default_key_func` (client IP)."""

    db_key: str | None = None
    """Which configured database stores the counters.  ``None``
    selects the database marked ``default=True``."""

    headers_enabled: bool = True
    """Emit ``X-RateLimit-*`` response headers (slowapi default on)."""


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

        Matches what :class:`~be.operations.scaffold.Scaffold` emits at
        ``db/{key}_session.py``.
        """
        return f"db.{self.key}_session"

    @property
    def get_db_fn(self) -> str:
        """Name of the FastAPI dependency exposed by the session module."""
        return f"get_{self.key}_db"


class FieldSpec(BaseModel):
    """A named, typed field — used in operation schemas and action params.

    Most fields are scalars: ``{name, type}`` where ``type`` is one
    of the :data:`~be.config.schema.FieldType` values.  A field
    can also be *nested* — a dump of a related model — by setting
    ``type: "nested"`` and
    supplying ``model`` (dotted import path to the related
    SQLAlchemy class) and ``fields`` (the sub-field list).  Set
    ``many=True`` when the relationship returns a collection.

    Nested fields are only meaningful on read-op dumps (``get``,
    ``list``).  Write-op request schemas (``create`` / ``update``)
    don't traverse them today — a validator enforces that.
    """

    name: str
    type: FieldType | Literal["nested"]
    model: str | None = None
    """Dotted import path of the related SQLAlchemy model, e.g.
    ``"blog.models.Project"``.  Required when ``type == "nested"``;
    must be omitted otherwise."""
    fields: list[FieldSpec] | None = None
    """Sub-field list for a nested dump.  Required when
    ``type == "nested"``; must be omitted otherwise."""
    many: bool = False
    """``True`` when the relationship returns a collection (list).
    Only meaningful when ``type == "nested"``."""
    load: LoaderStrategy = _DEFAULT_LOAD
    """Eager-loading strategy applied to this relationship in the
    generated ``select(...)`` statement.  Defaults to ``"selectin"``
    which issues one additional SELECT per relationship (safe for
    both scalar and collection relationships and avoids N+1).  Use
    ``"joined"`` for a single-query JOIN (better for one-to-one /
    many-to-one scalars) or ``"subquery"`` for an older-style
    correlated subquery load.  Only meaningful when
    ``type == "nested"``."""

    @model_validator(mode="after")
    def _validate_nested(self) -> FieldSpec:
        if self.type == NESTED:
            if self.model is None or self.fields is None:
                msg = (
                    f"Field {self.name!r}: nested fields require "
                    f"`model` and `fields`."
                )
                raise ValueError(msg)

            if not self.fields:
                msg = f"Field {self.name!r}: nested `fields` must be non-empty."
                raise ValueError(msg)

        else:
            if self.model is not None or self.fields is not None:
                msg = (
                    f"Field {self.name!r}: `model` and `fields` are "
                    f'only allowed when `type: "nested"`.'
                )
                raise ValueError(msg)

            if self.many:
                msg = (
                    f"Field {self.name!r}: `many` is only meaningful "
                    f'when `type: "nested"`.'
                )
                raise ValueError(msg)

            if self.load != _DEFAULT_LOAD:
                msg = (
                    f"Field {self.name!r}: `load` is only meaningful "
                    f'when `type: "nested"`.'
                )
                raise ValueError(msg)

        return self

    @property
    def is_nested(self) -> bool:
        """Whether this spec describes a nested dump of a related model."""
        return self.type == NESTED


class ModifierConfig(BaseModel):
    """Configuration for an op modifier.

    Modifiers nest inside their parent op's config (under
    ``modifiers: [...]``) and augment the parent's outputs.  The
    ``type`` field discriminates which modifier op consumes the
    entry — ``"filter"`` routes to :class:`~be.operations.filter.Filter`,
    ``"order"`` to :class:`~be.operations.order.Order`, etc.  All
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
    trace: bool | None = None
    """Per-operation telemetry override.  When ``None``, inherits
    the resource-level ``trace`` default (which itself inherits
    from the project's :attr:`TelemetryConfig.span_per_handler` /
    :attr:`TelemetryConfig.span_per_action`).  Set ``False`` to
    skip span emission for noisy / hot-path operations."""
    rate_limit: str | Literal[False] | None = None
    """Per-operation rate-limit override.  ``None`` inherits from
    the resource (which inherits from
    :attr:`RateLimitConfig.default_limit`).  ``False`` disables
    rate limiting on this op specifically.  A string (e.g.
    ``"5/minute"``) sets a per-op limit using the ``limits``
    library's syntax."""
    can: str | None = None
    """Dotted path to an async ``(resource, session) -> bool`` guard.

    The same callable serves two purposes: it gates execution of
    the operation (handlers raise 403 when it returns False) and
    it decides whether the operation appears in serialized
    ``actions`` lists for visibility.  ``None`` means "always
    available to authenticated users" -- bound to
    :func:`ingot.actions.always_true` at generation time.

    Object-scope ops (``get``, ``update``, ``delete``, custom
    object actions) receive the resource instance; collection-
    scope ops (``list``, ``create``, custom collection actions)
    receive ``None`` as the resource argument.  ``can_list`` is
    additionally used as the row-level visibility filter on the
    list endpoint, so it sees each candidate row in turn.
    """
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

    trace: bool | None = None
    """Per-resource telemetry override.  When ``None``, inherits the
    project-level :attr:`TelemetryConfig.span_per_handler` /
    :attr:`TelemetryConfig.span_per_action` toggles.  Set ``False``
    to skip per-handler spans for every op on this resource (the
    HTTP server span from ``FastAPIInstrumentor`` is unaffected)."""

    rate_limit: str | Literal[False] | None = None
    """Per-resource rate-limit override.  ``None`` inherits from
    :attr:`RateLimitConfig.default_limit`.  ``False`` disables
    rate limiting for every op on this resource.  A string (e.g.
    ``"100/minute"``) applies that limit to every op that doesn't
    set its own ``rate_limit``."""

    operations: Annotated[list[OperationConfig], Scoped(name="operation")] = (
        Field(default_factory=list)
    )
    """Ordered list of operations to run — each becomes a scope
    instance of ``"operation"`` that the engine visits in turn."""

    include_actions_in_dump: bool = False
    """When ``True``, every dumped representation of this resource
    (object responses and list rows) gains an ``actions`` field
    listing the operations the current session may take.  The list
    envelope of the list endpoint also gains a collection-scoped
    ``actions`` field.  Reserves the name ``"actions"``: no
    :class:`FieldSpec` on any of this resource's ops may use it."""

    permissions_endpoint: bool = False
    """When ``True``, generate ``GET /{prefix}/permissions`` (collection)
    and ``GET /{prefix}/{pk}/permissions`` (object) returning the
    available actions for the current session without paying for a
    full resource fetch.  Independent of
    :attr:`include_actions_in_dump`."""

    generate_tests: bool = False
    """When ``True``, emit a pytest test file for this resource's
    generated routes and serializers."""

    @model_validator(mode="after")
    def _reserve_actions_field_name(self) -> ResourceConfig:
        """Reject ``actions`` as a field name when the dump is on.

        ``include_actions_in_dump`` injects an ``actions`` key into
        the response schema; a consumer-declared field of the same
        name would silently collide.  Walks each op's raw
        ``fields`` extra (the same path the op's ``Options`` model
        will parse) so the error fires at config-load time, not
        downstream during template rendering.
        """
        if not self.include_actions_in_dump:
            return self

        for op in self.operations:
            fields = op.options.get("fields")

            if not isinstance(fields, list):
                continue

            for field in fields:
                if isinstance(field, dict) and field.get("name") == "actions":
                    msg = (
                        f"Resource {self.model!r} sets "
                        f"include_actions_in_dump=True, which reserves "
                        f"the field name 'actions'.  Operation "
                        f"{op.name!r} declares a field named 'actions' "
                        f"-- rename it."
                    )
                    raise ValueError(msg)

        return self


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
    """Top-level be configuration.

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
    telemetry: TelemetryConfig | None = None
    """OpenTelemetry configuration.  ``None`` (the default) means
    the generated app emits zero telemetry references; set to a
    :class:`TelemetryConfig` to opt in."""
    rate_limit: RateLimitConfig | None = None
    """Rate-limiting configuration.  ``None`` (the default) means the
    generated app emits zero rate-limit references; set to a
    :class:`RateLimitConfig` to opt in.  Per-resource and per-op
    ``rate_limit`` overrides are only allowed when this is set."""
    databases: list[DatabaseConfig] = Field(..., min_length=1)
    apps: Annotated[list[App], Scoped(name="app")] = Field(
        default_factory=list,
    )

    @model_validator(mode="after")
    def _rate_limit_overrides_require_project_config(
        self,
    ) -> ProjectConfig:
        """Reject per-resource/op ``rate_limit`` without project config.

        A non-``None`` ``rate_limit`` on a resource or operation
        references the limiter scaffolded by
        :class:`~be.operations.rate_limit_scaffold.RateLimitScaffold`,
        which only emits when ``project.rate_limit`` is set.  Failing
        at config-load time keeps the broken path from ever reaching
        template rendering.
        """
        if self.rate_limit is not None:
            return self

        for app in self.apps:
            for resource in app.config.resources:
                if resource.rate_limit is not None:
                    msg = (
                        f"Resource {resource.model!r} sets "
                        f"rate_limit={resource.rate_limit!r} but the "
                        f"project has no rate_limit configured.  "
                        f"Configure project.rate_limit or drop the "
                        f"override."
                    )
                    raise ValueError(msg)

                for op in resource.operations:
                    if op.rate_limit is not None:
                        msg = (
                            f"Resource {resource.model!r} operation "
                            f"{op.name!r} sets "
                            f"rate_limit={op.rate_limit!r} but the "
                            f"project has no rate_limit configured.  "
                            f"Configure project.rate_limit or drop the "
                            f"override."
                        )
                        raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _rate_limit_db_key_resolves(self) -> ProjectConfig:
        """``rate_limit.db_key`` must match a configured database.

        ``None`` defers to :meth:`resolve_database`, which picks the
        ``default=True`` entry; a string must name an existing
        :attr:`~DatabaseConfig.key`.
        """
        if self.rate_limit is None:
            return self

        # ``resolve_database`` raises with a clear message when the
        # key is missing or no default is set.
        self.resolve_database(self.rate_limit.db_key)
        return self

    @model_validator(mode="after")
    def _action_framework_requires_auth(self) -> ProjectConfig:
        """Reject opt-ins to the action framework without auth.

        The action framework's whole job is to gate by session --
        ``can`` callables receive ``(resource, session)``, the dump
        path threads ``session`` into the serializer, and the
        permissions endpoints look it up via ``Depends(get_session)``.
        With ``project.auth=None`` there is no session to forward,
        and the generated code would reference an undeclared
        parameter.  Failing at config-load time keeps the broken
        path from ever reaching template rendering.
        """
        if self.auth is not None:
            return self

        for app in self.apps:
            for resource in app.config.resources:
                if resource.include_actions_in_dump:
                    msg = (
                        f"Resource {resource.model!r} sets "
                        f"include_actions_in_dump=True but the project "
                        f"has no auth configured.  The action dump path "
                        f"requires a session; configure project.auth or "
                        f"drop the flag."
                    )
                    raise ValueError(msg)

                if resource.permissions_endpoint:
                    msg = (
                        f"Resource {resource.model!r} sets "
                        f"permissions_endpoint=True but the project "
                        f"has no auth configured.  The /permissions "
                        f"endpoints evaluate guards against a session; "
                        f"configure project.auth or drop the flag."
                    )
                    raise ValueError(msg)

                for op in resource.operations:
                    if op.can is not None:
                        msg = (
                            f"Resource {resource.model!r} operation "
                            f"{op.name!r} sets can={op.can!r} but the "
                            f"project has no auth configured.  The "
                            f"guard takes (resource, session); without "
                            f"auth there is no session to pass.  "
                            f"Configure project.auth or remove the can."
                        )
                        raise ValueError(msg)

        return self

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
