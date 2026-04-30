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

from __future__ import annotations

from pydantic import Field, model_validator

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
        rate_limit: When ``True``, request the
            ``kiln-generator[rate-limit]`` extra (slowapi +
            limits) *and* stamp a default
            ``rate_limit: rate_limit.slowapi('...')`` block into
            ``config/project.jsonnet`` pointing at a placeholder
            bucket model dotted path.  The user owns the bucket
            model class and the migration -- be_root only stamps
            the wiring.
        comms: When ``True``, scaffold the communication-platform
            wiring: stamp a default ``comms: comms.platform({...})``
            block into ``config/project.jsonnet`` and emit a
            starter ``comms.py`` at the project root with stub
            context schemas, a stub transport, and a stub
            preference resolver.  Requires ``pgqueuer=True`` --
            the comms dispatch path is pgqueuer-backed, and the
            validator rejects the combination at config-load time.
            ``comms.py`` is ``if_exists="skip"`` like every other
            be_root output, so re-bootstrap won't reset the
            user's edits.
        notification_preferences: When ``True``, scaffold a
            database-backed preference layer for the comms
            platform: the generated ``comms.py`` swaps its stub
            :class:`~ingot.comms.PreferenceResolver` for a real
            ``DbPreferenceResolver`` that queries
            ``{module}.models.NotificationPreference`` (a
            user-supplied SQLAlchemy class mixing in
            :class:`ingot.comms.NotificationPreferenceMixin`),
            and the per-app ``config/{module}.jsonnet`` gains a
            full-CRUD resource for managing those rows.
            Requires ``comms=True``; the validator rejects
            ``notification_preferences`` without ``comms``.

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
    rate_limit: bool = Field(default=False)
    comms: bool = Field(default=False)
    notification_preferences: bool = Field(default=False)

    @model_validator(mode="after")
    def _comms_requires_pgqueuer(self) -> RootConfig:
        """Reject ``comms=True`` without ``pgqueuer=True``.

        The comms platform's dispatch path is pgqueuer-backed
        (see :class:`ingot.comms.send_communication` and
        :func:`ingot.comms.make_dispatch_entrypoint`), so a
        ``comms`` bootstrap without the queue-install + worker
        recipes the ``pgqueuer`` flag emits would produce a
        broken bootstrap.  Fail at config-load time so the bad
        flag combination is caught before any files are written.
        """
        if self.comms and not self.pgqueuer:
            msg = (
                "comms=True requires pgqueuer=True (the comms "
                "dispatch path is pgqueuer-backed; without it, "
                "the bootstrap would scaffold a worker that "
                "can't run).  Set pgqueuer: true in the bootstrap "
                "config."
            )
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _notification_preferences_requires_comms(self) -> RootConfig:
        """Reject ``notification_preferences=True`` without ``comms=True``.

        The notification-preferences scaffold extends the comms
        platform (real :class:`~ingot.comms.PreferenceResolver`,
        plus the per-app CRUD resource that manages the rows the
        resolver queries).  Without ``comms``, neither
        ``comms.py`` nor the project.jsonnet ``comms`` block
        exists, so the resolver and the resource would point at
        nothing.
        """
        if self.notification_preferences and not self.comms:
            msg = (
                "notification_preferences=True requires comms=True "
                "(the preferences scaffold extends the comms "
                "platform; without comms there's no resolver to "
                "wire).  Set comms: true in the bootstrap config."
            )
            raise ValueError(msg)

        return self
