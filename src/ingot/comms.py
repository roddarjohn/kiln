"""Communication-platform primitives for kiln-generated apps.

Sends typed messages over pluggable delivery methods (email, SMS,
push, ...) using pgqueuer as the transactional outbox.  The pieces:

* :class:`CommType` -- a named, schema-validated communication.
  Carries a Pydantic ``context_schema`` and a pair of template
  strings (subject + body); :class:`CommRegistry` holds the set the
  consumer's app supports.

* :class:`Renderer` (Protocol) -- turns a :class:`CommType` plus a
  validated context into a rendered ``(subject, body)`` pair.  The
  default :class:`JinjaRenderer` is in-process; the same surface
  fits an HTTP-call renderer that defers to a separate template
  service (a node renderer, MJML compiler, anything) without
  changing the platform's call site.

* :class:`MessageMixin` / :class:`RecipientMixin` /
  :class:`NotificationPreferenceMixin` -- SQLAlchemy mixins
  supplying the storage columns for the three tables every
  comm-platform install needs.  Same pgcraft-friendly idiom as
  the file / rate-limit mixins: consumer owns the table, we own
  the columns.

* :class:`Transport` (Protocol) -- a method-specific delivery
  adapter.  One per supported method (``email``, ``sms``, ...).
  Implementations live in consumer code or third-party adapters;
  this module ships :class:`LoggingTransport` for tests and local
  development.

* :class:`PreferenceResolver` (Protocol) -- "should this recipient
  receive this comm-type via this method?"  Looked up once per
  recipient inside :func:`send_communication`; an opted-out
  recipient yields no row and no job (the message row still records
  the attempt for audit).

* :func:`send_communication` -- the producer entry point.  Validates
  context against the :class:`CommType` schema, renders the
  templates, inserts one message row and one recipient row per
  delivery, then enqueues one pgqueuer job per recipient under
  :data:`DISPATCH_ENTRYPOINT`.  All writes ride the caller's
  SQLAlchemy session and the pgqueuer ``Queries`` is bound to the
  same connection (see :func:`ingot.queue.get_queue`), so a single
  ``await session.commit()`` makes the message + recipients +
  jobs durable atomically.

* :func:`make_dispatch_entrypoint` -- the worker-side counterpart.
  Returns an async ``(job) -> None`` callable wired to the consumer's
  session factory, transports, and mixin classes -- register it
  against :data:`DISPATCH_ENTRYPOINT` on a pgqueuer ``PgQueuer``
  instance.

The module's only optional dependency is ``jinja2`` (already pulled
in by ``kiln-generator`` for codegen); no extras gate is needed.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from jinja2 import Template
from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    String,
    Text,
    Uuid,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pgqueuer import Queries
    from pgqueuer.models import Job
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


DISPATCH_ENTRYPOINT = "ingot_comms_dispatch"
"""pgqueuer entrypoint name jobs are enqueued under.

The producer side (:func:`send_communication`) enqueues under this
name; the worker side must register its handler under the same name
(see :func:`make_dispatch_entrypoint`).  Exposed so consumers don't
hard-code the literal in two places.
"""


class DeliveryStatus(enum.StrEnum):
    """Lifecycle states of a single recipient's delivery attempt."""

    PENDING = "pending"
    """Row inserted, job enqueued, transport not yet called."""

    SENT = "sent"
    """Transport returned without raising."""

    FAILED = "failed"
    """Transport raised; ``error`` carries the message."""


# -------------------------------------------------------------------
# CommType + registry
# -------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CommType[ContextT: BaseModel]:
    """A named communication: schema + templates + default methods.

    A ``CommType`` is the unit of design for the comm platform: it
    binds a Pydantic context schema to a pair of template strings
    plus the set of methods the consumer wants delivery on by
    default.  The same instance is shared by the producer (which
    validates and renders) and the worker (which renders too if the
    rendered body wasn't persisted).

    Attributes:
        name: Stable identifier (e.g. ``"order_shipped"``).  Used as
            the registry key and stored on the message row so the
            audit log survives template churn.
        context_schema: Pydantic model describing the fields the
            templates reference.  ``send_communication`` validates
            the caller-supplied context against this before
            rendering, so missing or mistyped fields fail fast at
            the call site instead of half-way through a template.
        subject_template: Source string for the subject line.
            Interpreted by the configured :class:`Renderer`; the
            default :class:`JinjaRenderer` treats it as Jinja2.
        body_template: Source string for the body.  Same renderer
            treatment as :attr:`subject_template`.
        default_methods: Methods to deliver on when the caller
            doesn't pass an explicit recipient list.  Empty tuple
            means "no default; caller must specify recipients".

    """

    name: str
    context_schema: type[ContextT]
    subject_template: str
    body_template: str
    default_methods: tuple[str, ...] = ()


class CommRegistry:
    """Mutable registry of :class:`CommType` entries by name.

    Built once at app startup (or as a module-level global if the
    consumer prefers) and passed into :func:`send_communication` and
    :func:`make_dispatch_entrypoint`.  Not thread-safe -- mutate it
    only during startup.
    """

    def __init__(self) -> None:
        """Build an empty registry."""
        self._types: dict[str, CommType[Any]] = {}

    def register(self, comm_type: CommType[Any]) -> None:
        """Add *comm_type* to the registry.

        Raises:
            ValueError: If a type with the same name is already
                registered.  Re-registration is almost always a bug
                (two modules thought they owned the same name);
                force callers to deregister first if they really
                want it.

        """
        if comm_type.name in self._types:
            msg = f"comm_type {comm_type.name!r} already registered"
            raise ValueError(msg)

        self._types[comm_type.name] = comm_type

    def get(self, name: str) -> CommType[Any]:
        """Return the :class:`CommType` registered under *name*.

        Raises:
            KeyError: If *name* is not registered.

        """
        try:
            return self._types[name]

        except KeyError:
            msg = f"unknown comm_type: {name!r}"
            raise KeyError(msg) from None

    def names(self) -> tuple[str, ...]:
        """Return registered comm-type names in registration order."""
        return tuple(self._types)


# -------------------------------------------------------------------
# Renderer
# -------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RenderedMessage:
    """Output of a :class:`Renderer` -- the strings we persist."""

    subject: str
    body: str


class Renderer(Protocol):
    """Hook: turn a :class:`CommType` + context into rendered strings.

    The default :class:`JinjaRenderer` evaluates the template strings
    in-process.  A microservice-based renderer (e.g. a node service
    that compiles MJML or runs a richer template language) implements
    the same single method and gets dropped in via the ``renderer``
    argument to :func:`send_communication` -- no other code changes.

    *context* is the validated Pydantic model; implementations call
    :meth:`~pydantic.BaseModel.model_dump` themselves so they can
    pick the dump mode (``json`` vs Python) that fits their wire
    format.
    """

    async def render(
        self,
        comm_type: CommType[Any],
        context: BaseModel,
    ) -> RenderedMessage:
        """Return the rendered subject + body for this comm."""
        ...


class JinjaRenderer:
    """In-process Jinja2 renderer.  The default for :func:`send_communication`.

    Treats :attr:`CommType.subject_template` and
    :attr:`CommType.body_template` as Jinja2 source strings and
    renders them against the context dump (``model_dump(mode="json")``
    so dates/uuids stringify the same way they would over the wire).

    Trivial enough to construct inline; instances hold no state
    beyond the autoescape choice, which only matters for the body
    of HTML emails (callers building HTML bodies should pass
    ``autoescape=True``).
    """

    def __init__(self, *, autoescape: bool = False) -> None:
        """Build a renderer.

        Args:
            autoescape: When ``True``, Jinja autoescapes HTML in the
                rendered output.  Off by default because plain-text
                bodies (SMS, push) are the more common case;
                consumers building HTML email bodies should opt in.

        """
        self._autoescape = autoescape

    async def render(
        self,
        comm_type: CommType[Any],
        context: BaseModel,
    ) -> RenderedMessage:
        """Render the comm's templates against *context*."""
        data = context.model_dump(mode="json")
        subject = Template(
            comm_type.subject_template,
            autoescape=self._autoescape,
        ).render(**data)
        body = Template(
            comm_type.body_template,
            autoescape=self._autoescape,
        ).render(**data)
        return RenderedMessage(subject=subject, body=body)


# -------------------------------------------------------------------
# Mixins
# -------------------------------------------------------------------


class MessageMixin:
    """SQLAlchemy mixin for the message table.

    One row per ``send_communication`` call -- represents the
    *intent* to communicate.  Per-method delivery state lives on the
    :class:`RecipientMixin` rows that point back here via
    :attr:`RecipientMixin.message_id`.

    Subclass on a regular ``Base`` to materialise the table:

    .. code-block:: python

        from ingot.comms import MessageMixin

        class CommMessage(Base, MessageMixin):
            __tablename__ = "comm_messages"

    Storing the rendered ``subject``/``body`` (rather than re-rendering
    from ``context`` at send time) means template churn doesn't
    invalidate the audit log: the message row reflects what the
    recipient actually saw.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    """Server-generated identifier; stable across renders."""

    comm_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    """Registry key (:attr:`CommType.name`).  Indexed so the
    audit log can group by type cheaply."""

    context: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    """JSON dump of the validated context, kept so the row can be
    re-rendered or replayed if templates change."""

    subject: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    """Rendered subject.  Nullable because some methods (SMS, push)
    have only a body."""

    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """Rendered body.  ``Text`` rather than ``String`` because
    bodies (especially HTML email) routinely exceed the 64 KiB
    Postgres ``varchar`` ceiling."""

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: _dt.datetime.now(tz=_dt.UTC),
        nullable=False,
    )
    """When :func:`send_communication` inserted this row."""


class RecipientMixin:
    """SQLAlchemy mixin for the recipient table.

    One row per ``(message, method, address)`` triple.  The pgqueuer
    job carries the recipient id, so the dispatch path is:
    job -> recipient -> message -> transport lookup.

    The mixin deliberately doesn't declare a foreign key to the
    consumer's :class:`MessageMixin` table -- the consumer names
    that table, so the FK has to come from their own subclass via
    :class:`~sqlalchemy.ForeignKey` on the ``message_id`` column.
    Keeping the mixin FK-free means the same class works regardless
    of where the consumer mounts the message table.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    """Server-generated identifier; doubles as the pgqueuer job
    payload."""

    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
        index=True,
    )
    """Points back at the :class:`MessageMixin` row.  Consumers
    typically add an explicit ``ForeignKey`` in their subclass."""

    method: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    """Delivery method (``"email"``, ``"sms"``, ...).  Used to look
    up the right transport at dispatch time."""

    address: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    """Method-specific destination (email address, phone number,
    push token, ...).  Opaque to this module."""

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DeliveryStatus.PENDING.value,
    )
    """One of :class:`DeliveryStatus`'s values.  Stored as a string
    (not a SQL enum) so adding a new state doesn't require a
    migration."""

    sent_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the transport returned successfully.  ``None`` while
    pending or failed."""

    error: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    """Truncated exception message from a failed delivery.  ``None``
    until the dispatch path catches an error."""


class NotificationPreferenceMixin:
    """SQLAlchemy mixin for the per-recipient preference table.

    One row per ``(subject_key, comm_type, method)`` triple records
    whether that recipient wants that comm type via that method.
    :func:`send_communication` looks the row up via the
    :class:`PreferenceResolver` protocol -- this mixin just supplies
    the columns; the resolver implementation lives in consumer code
    (or in a generated helper).

    ``subject_key`` is intentionally a string rather than a typed
    foreign key: a comm platform routinely targets users, accounts,
    org-level addresses, or external identifiers, and a typed FK
    would lock the table to one of those.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    subject_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    """Identifier of the recipient whose preferences this row
    captures (typically a user id formatted as a string)."""

    comm_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    """Registry key (:attr:`CommType.name`)."""

    method: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    """Delivery method this preference scopes."""

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    """``True`` when the recipient consents to this
    ``(comm_type, method)`` combination.  Default ``True`` so an
    absent row reads as opt-in by default; flip per consumer policy
    if your default is opt-out."""


# -------------------------------------------------------------------
# Recipients + preferences + transports
# -------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RecipientSpec:
    """Single recipient handed to :func:`send_communication`.

    Attributes:
        method: Delivery method (must match a transport key).
        address: Method-specific destination (email, phone, ...).
        subject_key: Identifier whose preferences gate delivery.
            ``None`` skips the preference check (e.g. transactional
            sends to non-user addresses like a billing inbox).

    """

    method: str
    address: str
    subject_key: str | None = None


class PreferenceResolver(Protocol):
    """Hook: gate delivery on the recipient's per-method opt-in."""

    async def is_enabled(
        self,
        *,
        subject_key: str,
        comm_type: str,
        method: str,
    ) -> bool:
        """Return ``True`` to deliver, ``False`` to suppress."""
        ...


class Transport(Protocol):
    """Method-specific delivery adapter.

    Implementations are free to do whatever the method requires
    (SMTP send, Twilio API call, FCM push, ...).  Raise to mark the
    delivery failed; return normally to mark it sent.  The dispatch
    path stamps :attr:`RecipientMixin.status` and
    :attr:`RecipientMixin.sent_at` based on which path you take.
    """

    async def send(
        self,
        *,
        message: MessageMixin,
        recipient: RecipientMixin,
    ) -> None:
        """Deliver *message* to *recipient* or raise."""
        ...


class LoggingTransport:
    """Test/dev transport: records every send into an in-memory list.

    Not async-safe across processes, obviously; the point is to give
    unit tests something to assert against and to give local
    development a no-credentials fallback.  Production transports
    live in consumer code or third-party packages.
    """

    def __init__(self) -> None:
        """Build a transport with an empty :attr:`sent` log."""
        self.sent: list[tuple[uuid.UUID, str, str]] = []

    async def send(
        self,
        *,
        message: MessageMixin,
        recipient: RecipientMixin,
    ) -> None:
        """Record ``(message_id, address, body)`` into :attr:`sent`."""
        self.sent.append((message.id, recipient.address, message.body))


# -------------------------------------------------------------------
# Producer side -- the entry point a request handler calls.
# -------------------------------------------------------------------


async def send_communication(
    *,
    session: AsyncSession,
    queue: Queries,
    registry: CommRegistry,
    comm_type: str,
    context: BaseModel | dict[str, Any],
    recipients: Sequence[RecipientSpec],
    message_cls: type[MessageMixin],
    recipient_cls: type[RecipientMixin],
    renderer: Renderer | None = None,
    preferences: PreferenceResolver | None = None,
) -> uuid.UUID:
    """Validate, render, persist, and enqueue a communication.

    The transactional-outbox guarantee:

    1. Validate *context* against the comm-type's schema.
    2. Render the templates with *renderer* (defaults to
       :class:`JinjaRenderer`).
    3. Insert one :class:`MessageMixin` row.
    4. For each recipient, consult *preferences* (if supplied);
       insert a :class:`RecipientMixin` row for each one that
       passes.
    5. Enqueue one pgqueuer job per surviving recipient under
       :data:`DISPATCH_ENTRYPOINT`, payload = recipient id (UTF-8).

    Steps 3-5 all ride *session*'s transaction (see
    :func:`ingot.queue.get_queue` for how *queue* is bound to the
    same connection).  Commit the session and the message,
    recipients, and jobs all become durable atomically; roll back
    and the communication never happened.

    Args:
        session: The async SQLAlchemy session running the caller's
            transaction.  All inserts ride it.
        queue: A pgqueuer ``Queries`` bound to *session*'s
            connection.  Build it with
            :func:`ingot.queue.get_queue` immediately before this
            call.
        registry: The :class:`CommRegistry` containing the type
            named by *comm_type*.
        comm_type: Registry key for the comm to send.
        context: Either an instance of the type's
            :attr:`CommType.context_schema` or a dict that will be
            validated against it.
        recipients: Per-delivery specs (method + address +
            optional subject_key for preference lookup).
        message_cls: Consumer's concrete :class:`MessageMixin`
            subclass.
        recipient_cls: Consumer's concrete :class:`RecipientMixin`
            subclass.
        renderer: Override for the template renderer.  Defaults to
            :class:`JinjaRenderer` -- swap in an HTTP-call renderer
            to defer rendering to a separate service.
        preferences: Optional preference resolver.  When omitted,
            every recipient is delivered to (subject to
            ``subject_key`` semantics on :class:`RecipientSpec`).

    Returns:
        The id of the inserted :class:`MessageMixin` row.

    """
    spec = registry.get(comm_type)

    if isinstance(context, BaseModel):
        validated: BaseModel = context

    else:
        validated = spec.context_schema.model_validate(context)

    used_renderer = renderer or JinjaRenderer()
    rendered = await used_renderer.render(spec, validated)

    message_id = uuid.uuid4()
    await session.execute(
        insert(message_cls).values(
            id=message_id,
            comm_type=comm_type,
            context=validated.model_dump(mode="json"),
            subject=rendered.subject,
            body=rendered.body,
        ),
    )

    delivered_ids: list[uuid.UUID] = []

    for recipient in recipients:
        if (
            preferences is not None
            and recipient.subject_key is not None
            and not await preferences.is_enabled(
                subject_key=recipient.subject_key,
                comm_type=comm_type,
                method=recipient.method,
            )
        ):
            continue

        rid = uuid.uuid4()
        await session.execute(
            insert(recipient_cls).values(
                id=rid,
                message_id=message_id,
                method=recipient.method,
                address=recipient.address,
                status=DeliveryStatus.PENDING.value,
            ),
        )
        delivered_ids.append(rid)

    if delivered_ids:
        await queue.enqueue(
            [DISPATCH_ENTRYPOINT] * len(delivered_ids),
            [str(rid).encode("utf-8") for rid in delivered_ids],
            [0] * len(delivered_ids),
        )

    return message_id


# -------------------------------------------------------------------
# Worker side -- the pgqueuer entrypoint factory.
# -------------------------------------------------------------------


def make_dispatch_entrypoint(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transports: dict[str, Transport],
    message_cls: type[MessageMixin],
    recipient_cls: type[RecipientMixin],
) -> Callable[[Job], Awaitable[None]]:
    """Build the worker-side handler for :data:`DISPATCH_ENTRYPOINT`.

    Returns an ``async (job) -> None`` callable suitable for
    :meth:`pgqueuer.PgQueuer.entrypoint`.  Per job:

    1. Decode the recipient id from ``job.payload``.
    2. Open a session from *session_factory*; load the recipient
       and the matching message.
    3. Skip if the recipient is missing or already advanced past
       :attr:`DeliveryStatus.PENDING` (job retried after a previous
       success / explicit failure).
    4. Look up the transport for the recipient's method; mark the
       row failed if no transport is registered.
    5. Call ``transport.send`` -- mark the row sent on success or
       failed (with the error message) on raise.

    Args:
        session_factory: Async sessionmaker; each job opens a
            short-lived session of its own.
        transports: Method -> :class:`Transport` lookup.
        message_cls: Consumer's concrete :class:`MessageMixin`.
        recipient_cls: Consumer's concrete :class:`RecipientMixin`.

    Returns:
        Async handler the consumer registers under
        :data:`DISPATCH_ENTRYPOINT`.

    """

    async def handler(job: Job) -> None:
        if job.payload is None:
            # An empty payload can't identify a recipient.  Drop the
            # job rather than raising -- pgqueuer's retry path would
            # just hit the same condition again.
            return

        recipient_id = uuid.UUID(job.payload.decode("utf-8"))

        async with session_factory() as session:
            recipient = await session.get(recipient_cls, recipient_id)

            if recipient is None:
                return

            if recipient.status != DeliveryStatus.PENDING.value:
                # Job re-fire after a previous terminal outcome --
                # leave the row alone so audit history stays honest.
                return

            message = await session.get(message_cls, recipient.message_id)

            if message is None:
                await _mark_failed(
                    session,
                    recipient_cls,
                    recipient_id,
                    error="message row missing",
                )
                await session.commit()
                return

            transport = transports.get(recipient.method)

            if transport is None:
                await _mark_failed(
                    session,
                    recipient_cls,
                    recipient_id,
                    error=f"no transport for method {recipient.method!r}",
                )
                await session.commit()
                return

            try:
                await transport.send(message=message, recipient=recipient)

            except Exception as exc:  # noqa: BLE001 -- transport-agnostic
                await _mark_failed(
                    session,
                    recipient_cls,
                    recipient_id,
                    error=_truncate(str(exc)),
                )
                await session.commit()
                return

            await session.execute(
                update(recipient_cls)
                .where(recipient_cls.id == recipient_id)
                .values(
                    status=DeliveryStatus.SENT.value,
                    sent_at=_dt.datetime.now(tz=_dt.UTC),
                    error=None,
                ),
            )
            await session.commit()

    return handler


_ERROR_COLUMN_LIMIT = 1024
"""Length cap matching :attr:`RecipientMixin.error`'s column type."""


def _truncate(text: str) -> str:
    """Clip *text* to fit :attr:`RecipientMixin.error`."""
    if len(text) <= _ERROR_COLUMN_LIMIT:
        return text

    return text[: _ERROR_COLUMN_LIMIT - 1] + "…"


async def _mark_failed(
    session: AsyncSession,
    recipient_cls: type[RecipientMixin],
    recipient_id: uuid.UUID,
    *,
    error: str,
) -> None:
    """Stamp a recipient row as :attr:`DeliveryStatus.FAILED`."""
    await session.execute(
        update(recipient_cls)
        .where(recipient_cls.id == recipient_id)
        .values(
            status=DeliveryStatus.FAILED.value,
            error=_truncate(error),
        ),
    )


# -------------------------------------------------------------------
# Convenience: load the audit row
# -------------------------------------------------------------------


async def load_recipients(
    session: AsyncSession,
    recipient_cls: type[RecipientMixin],
    message_id: uuid.UUID,
) -> list[RecipientMixin]:
    """Return every recipient row for *message_id*, in insertion order.

    Lightweight read helper for an audit endpoint -- generated route
    handlers can call this without re-deriving the ``message_id ==``
    filter.
    """
    result = await session.execute(
        select(recipient_cls).where(recipient_cls.message_id == message_id),
    )
    return list(result.scalars())
