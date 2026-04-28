"""Pydantic schema for be_root configs.

The schema is small but not bare: a few identity fields plus
a handful of opt-in toggles for the optional bits real apps
almost always need (OpenTelemetry init, files extra, psycopg
sync engine, pgcraft, pgqueuer recipes, editable local installs).
The defaults stay opinionated -- ``pyjwt`` and ``python-multipart``
are always present, the per-file ruff ignore block is always
emitted -- so a default-defaulted bootstrap drops you into a
sensible FastAPI shape without a mountain of dead config.
"""

from pydantic import Field

from foundry.config import FoundryConfig


class RootConfig(FoundryConfig):
    """Top-level config for the ``be_root`` target.

    Attributes:
        name: Project name.  Used as the ``[project].name`` value
            in the generated ``pyproject.toml`` and as the
            FastAPI app's ``title``.  Should be a valid Python
            distribution name (lowercase, hyphens or
            underscores).
        module: Default Python package name for the user's first
            be app.  The generated starter ``config/{module}.jsonnet``
            file references it, and the bootstrap creates an
            empty ``{module}/`` package so be has somewhere to
            attach generated routes on the first ``just generate``.
        description: Free-form one-line description.  Flows into
            ``pyproject.toml`` and the FastAPI app's
            ``description`` for parity with the generated OpenAPI
            spec.
        opentelemetry: When ``True``, request the
            ``kiln-generator[opentelemetry]`` extra in
            ``pyproject.toml``, emit the ``init_telemetry(app)``
            call in ``main.py``, *and* stamp a default
            ``telemetry: telemetry.otel(name)`` block into
            ``config/project.jsonnet`` so be actually
            generates ``_generated/telemetry.py``.  All three
            sides flip together -- a half-set telemetry pulls
            an ``ImportError`` at startup.
        files: When ``True``, request the
            ``kiln-generator[files]`` extra (boto3 + the
            ``ingot.files`` runtime helpers) for projects that
            expose file-upload endpoints.
        auth: When ``True``, scaffold a starter auth wiring:
            an ``auth.py`` skeleton at the project root with
            stub ``LoginCredentials`` / ``Session`` /
            ``validate_login`` symbols (the user fills in
            credential validation), and an ``auth: auth.jwt(...)``
            block in ``config/project.jsonnet`` pointing at
            those symbols.  ``pyjwt`` is in the default deps so
            no extra is needed.  ``auth.py`` is
            ``if_exists="skip"`` like every other be_root
            output, so re-bootstrap won't reset the user's
            real ``validate_login`` implementation.
        psycopg: When ``True``, add ``psycopg[binary]`` to the
            base dependency list.  Required by be's
            sync-engine login path: the generated
            ``POST /auth/token`` handler is a plain ``def`` and
            can't ride the asyncpg pool.
        pgcraft: When ``True``, add ``pgcraft`` to the base
            dependency list.  Pulls in the migration / schema
            tooling be integrates with.
        pgqueuer: When ``True``, emit ``queue-install`` and
            ``worker`` recipes in the generated ``justfile`` for
            apps that run a pgqueuer-backed background-job
            queue alongside the API.
        editable: When ``True``, emit a ``[tool.uv.sources]``
            block pinning ``kiln-generator`` (and ``pgcraft``,
            when enabled) to editable sibling-repo installs at
            ``../be`` / ``../pgcraft``.  Right for local
            development against unreleased changes; flip off
            when publishing.

    """

    name: str = Field(default="myapp")
    module: str = Field(default="app")
    description: str = Field(
        default="FastAPI app bootstrapped by be_root.",
    )
    opentelemetry: bool = Field(default=False)
    files: bool = Field(default=False)
    auth: bool = Field(default=False)
    psycopg: bool = Field(default=False)
    pgcraft: bool = Field(default=False)
    pgqueuer: bool = Field(default=False)
    editable: bool = Field(default=False)
