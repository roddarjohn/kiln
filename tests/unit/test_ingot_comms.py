"""Tests for ingot.comms.

Live PostgreSQL and pgqueuer aren't available in unit tests, so the
SQLAlchemy session and ``Queries`` are mocked.  Mixin coverage is via
real SQLAlchemy mapping against an in-memory metadata.
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase

import ingot.comms as comms_mod
from ingot.comms import (
    DISPATCH_ENTRYPOINT,
    CommRegistry,
    CommType,
    DeliveryStatus,
    JinjaRenderer,
    LoggingTransport,
    MessageMixin,
    NotificationPreferenceMixin,
    PreferenceResolver,
    RecipientMixin,
    RecipientSpec,
    RenderedMessage,
    Renderer,
    Transport,
    _truncate,
    load_recipients,
    make_dispatch_entrypoint,
    send_communication,
)
from ingot.utils import compile_query

# -------------------------------------------------------------------
# Test fixtures: concrete mapped classes for the three mixins
# -------------------------------------------------------------------


class _Base(DeclarativeBase):
    pass


class _Message(_Base, MessageMixin):
    __tablename__ = "_test_messages"


class _Recipient(_Base, RecipientMixin):
    __tablename__ = "_test_recipients"


class _Pref(_Base, NotificationPreferenceMixin):
    __tablename__ = "_test_prefs"


class _OrderShippedContext(BaseModel):
    order_id: str
    total: float


_ORDER_SHIPPED = CommType(
    name="order_shipped",
    context_schema=_OrderShippedContext,
    subject_template="Order {{ order_id }} shipped",
    body_template="Your order {{ order_id }} (${{ total }}) is on the way.",
    default_methods=("email",),
)


def _columns(model: type) -> dict[str, Any]:
    return {col.name: col for col in inspect(model).columns}


# -------------------------------------------------------------------
# CommRegistry
# -------------------------------------------------------------------


class TestCommRegistry:
    def test_register_and_get(self):
        reg = CommRegistry()
        reg.register(_ORDER_SHIPPED)
        assert reg.get("order_shipped") is _ORDER_SHIPPED

    def test_register_duplicate_raises(self):
        reg = CommRegistry()
        reg.register(_ORDER_SHIPPED)

        with pytest.raises(ValueError, match="already registered"):
            reg.register(_ORDER_SHIPPED)

    def test_get_unknown_raises(self):
        reg = CommRegistry()

        with pytest.raises(KeyError, match="unknown comm_type"):
            reg.get("nope")

    def test_names_preserves_registration_order(self):
        reg = CommRegistry()
        other = CommType(
            name="welcome",
            context_schema=_OrderShippedContext,
            subject_template="Hi",
            body_template="Hi",
        )
        reg.register(_ORDER_SHIPPED)
        reg.register(other)
        assert reg.names() == ("order_shipped", "welcome")


# -------------------------------------------------------------------
# JinjaRenderer
# -------------------------------------------------------------------


class TestJinjaRenderer:
    @pytest.mark.asyncio
    async def test_renders_subject_and_body(self):
        renderer = JinjaRenderer()
        ctx = _OrderShippedContext(order_id="42", total=19.5)
        rendered = await renderer.render(_ORDER_SHIPPED, ctx)
        assert rendered.subject == "Order 42 shipped"
        assert rendered.body == "Your order 42 ($19.5) is on the way."
        assert isinstance(rendered, RenderedMessage)

    @pytest.mark.asyncio
    async def test_autoescape_off_by_default(self):
        renderer = JinjaRenderer()
        comm = CommType(
            name="raw",
            context_schema=_OrderShippedContext,
            subject_template="x",
            body_template="<b>{{ order_id }}</b>",
        )
        ctx = _OrderShippedContext(order_id="<scr>", total=0.0)
        rendered = await renderer.render(comm, ctx)
        # Plain-text default: angle brackets pass through.
        assert rendered.body == "<b><scr></b>"

    @pytest.mark.asyncio
    async def test_autoescape_on_escapes_html(self):
        renderer = JinjaRenderer(autoescape=True)
        comm = CommType(
            name="html",
            context_schema=_OrderShippedContext,
            subject_template="x",
            body_template="<b>{{ order_id }}</b>",
        )
        ctx = _OrderShippedContext(order_id="<scr>", total=0.0)
        rendered = await renderer.render(comm, ctx)
        assert "&lt;scr&gt;" in rendered.body

    def test_satisfies_renderer_protocol(self):
        # Static structural check: a JinjaRenderer is-a Renderer.
        renderer: Renderer = JinjaRenderer()
        assert renderer is not None


# -------------------------------------------------------------------
# Mixin column coverage
# -------------------------------------------------------------------


class TestMixinColumns:
    def test_message_columns(self):
        cols = _columns(_Message)
        expected = {
            "id",
            "comm_type",
            "context",
            "subject",
            "body",
            "created_at",
        }
        assert expected <= set(cols)
        assert cols["comm_type"].nullable is False
        assert cols["body"].nullable is False
        assert cols["subject"].nullable is True
        assert cols["created_at"].nullable is False

    def test_recipient_columns(self):
        cols = _columns(_Recipient)
        expected = {
            "id",
            "message_id",
            "method",
            "address",
            "status",
            "sent_at",
            "error",
        }
        assert expected <= set(cols)
        assert cols["message_id"].nullable is False
        assert cols["status"].nullable is False
        assert cols["sent_at"].nullable is True
        assert cols["error"].nullable is True

    def test_pref_columns(self):
        cols = _columns(_Pref)
        expected = {"id", "subject_key", "comm_type", "method", "enabled"}
        assert expected <= set(cols)
        assert cols["enabled"].nullable is False


# -------------------------------------------------------------------
# send_communication
# -------------------------------------------------------------------


def _fake_session() -> AsyncMock:
    """Mock async session supporting ``await session.execute(stmt)``."""
    session = AsyncMock()
    session.execute = AsyncMock()
    return session


def _fake_queue() -> AsyncMock:
    """Mock pgqueuer ``Queries`` exposing ``await queue.enqueue(...)``."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    return queue


def _registry() -> CommRegistry:
    reg = CommRegistry()
    reg.register(_ORDER_SHIPPED)
    return reg


class TestSendCommunication:
    @pytest.mark.asyncio
    async def test_persists_message_and_recipients_and_enqueues(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        message_id = await send_communication(
            session=session,
            queue=queue,
            registry=reg,
            comm_type="order_shipped",
            context={"order_id": "42", "total": 19.5},
            recipients=[
                RecipientSpec(method="email", address="a@example.com"),
                RecipientSpec(method="sms", address="+15551111"),
            ],
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        # 1 message + 2 recipients = 3 inserts.
        assert session.execute.await_count == 3

        # One enqueue call carrying both recipient ids under
        # DISPATCH_ENTRYPOINT.
        queue.enqueue.assert_awaited_once()
        args = queue.enqueue.call_args.args
        eps, payloads, priorities = args
        assert eps == [DISPATCH_ENTRYPOINT, DISPATCH_ENTRYPOINT]
        assert priorities == [0, 0]
        # Payloads are recipient UUIDs encoded utf-8; both decode.
        assert all(uuid.UUID(p.decode("utf-8")) for p in payloads)

        # Returned message id is a real UUID.
        assert isinstance(message_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_validates_dict_context_against_schema(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        with pytest.raises(ValidationError):
            await send_communication(
                session=session,
                queue=queue,
                registry=reg,
                comm_type="order_shipped",
                # missing required ``total``
                context={"order_id": "42"},
                recipients=[],
                message_cls=_Message,
                recipient_cls=_Recipient,
            )

    @pytest.mark.asyncio
    async def test_basemodel_context_passes_through_unvalidated(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        # An already-built model is trusted -- pydantic re-validation
        # is the caller's choice.  Just confirm it renders.
        ctx = _OrderShippedContext(order_id="99", total=1.0)
        await send_communication(
            session=session,
            queue=queue,
            registry=reg,
            comm_type="order_shipped",
            context=ctx,
            recipients=[
                RecipientSpec(method="email", address="x@example.com"),
            ],
            message_cls=_Message,
            recipient_cls=_Recipient,
        )
        assert session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_no_recipients_skips_enqueue(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        await send_communication(
            session=session,
            queue=queue,
            registry=reg,
            comm_type="order_shipped",
            context={"order_id": "42", "total": 19.5},
            recipients=[],
            message_cls=_Message,
            recipient_cls=_Recipient,
        )
        # Just the message insert.
        assert session.execute.await_count == 1
        queue.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_preferences_filter_opted_out_recipients(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        class StaticPrefs:
            async def is_enabled(
                self,
                *,
                subject_key: str,  # noqa: ARG002
                comm_type: str,  # noqa: ARG002
                method: str,
            ) -> bool:
                return method == "email"  # SMS opted out

        prefs: PreferenceResolver = StaticPrefs()

        await send_communication(
            session=session,
            queue=queue,
            registry=reg,
            comm_type="order_shipped",
            context={"order_id": "42", "total": 19.5},
            recipients=[
                RecipientSpec(
                    method="email",
                    address="a@example.com",
                    subject_key="user-1",
                ),
                RecipientSpec(
                    method="sms",
                    address="+1",
                    subject_key="user-1",
                ),
            ],
            message_cls=_Message,
            recipient_cls=_Recipient,
            preferences=prefs,
        )

        # 1 message + 1 (email) recipient = 2 inserts.
        assert session.execute.await_count == 2
        # Only one job enqueued.
        eps, _, _ = queue.enqueue.call_args.args
        assert eps == [DISPATCH_ENTRYPOINT]

    @pytest.mark.asyncio
    async def test_subject_key_none_bypasses_preferences(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        # A resolver that always denies -- but the recipient has no
        # subject_key, so it never gets consulted.
        prefs = MagicMock()
        prefs.is_enabled = AsyncMock(return_value=False)

        await send_communication(
            session=session,
            queue=queue,
            registry=reg,
            comm_type="order_shipped",
            context={"order_id": "42", "total": 19.5},
            recipients=[
                RecipientSpec(method="email", address="ops@example.com"),
            ],
            message_cls=_Message,
            recipient_cls=_Recipient,
            preferences=prefs,
        )
        prefs.is_enabled.assert_not_called()
        assert session.execute.await_count == 2  # message + recipient

    @pytest.mark.asyncio
    async def test_uses_supplied_renderer(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        # Custom renderer just to prove the swap point: returns a
        # constant subject/body regardless of templates.
        class FixedRenderer:
            async def render(
                self,
                comm_type: CommType[Any],  # noqa: ARG002
                context: BaseModel,  # noqa: ARG002
            ) -> RenderedMessage:
                return RenderedMessage(subject="S", body="B")

        await send_communication(
            session=session,
            queue=queue,
            registry=reg,
            comm_type="order_shipped",
            context={"order_id": "1", "total": 0.0},
            recipients=[
                RecipientSpec(method="email", address="a@example.com"),
            ],
            message_cls=_Message,
            recipient_cls=_Recipient,
            renderer=FixedRenderer(),
        )

        # First execute() is the message insert -- inspect its bound
        # parameter values directly (literal-binding the JSON column
        # would force a renderer for dict, which sqlalchemy doesn't
        # ship by default).
        message_call = session.execute.await_args_list[0]
        stmt = message_call.args[0]
        params = stmt.compile().params
        assert params["subject"] == "S"
        assert params["body"] == "B"

    @pytest.mark.asyncio
    async def test_unknown_comm_type_raises(self):
        session = _fake_session()
        queue = _fake_queue()
        reg = _registry()

        with pytest.raises(KeyError, match="unknown comm_type"):
            await send_communication(
                session=session,
                queue=queue,
                registry=reg,
                comm_type="nope",
                context={},
                recipients=[],
                message_cls=_Message,
                recipient_cls=_Recipient,
            )


# -------------------------------------------------------------------
# Worker dispatch
# -------------------------------------------------------------------


def _job(payload: bytes) -> Any:
    """Minimal pgqueuer Job stand-in -- only ``payload`` is read."""
    j = MagicMock()
    j.payload = payload
    return j


def _stub_session_factory(session: AsyncMock) -> Any:
    """Return a sessionmaker-shaped factory yielding *session*."""

    @asynccontextmanager
    async def _ctx():
        yield session

    return _ctx


class _StubMessage:
    def __init__(self, mid: uuid.UUID, body: str = "hello") -> None:
        self.id = mid
        self.body = body


class _StubRecipient:
    def __init__(
        self,
        rid: uuid.UUID,
        message_id: uuid.UUID,
        method: str,
        address: str,
        status: str = DeliveryStatus.PENDING.value,
    ) -> None:
        self.id = rid
        self.message_id = message_id
        self.method = method
        self.address = address
        self.status = status


def _stub_session(
    *,
    recipient: _StubRecipient | None,
    message: _StubMessage | None = None,
) -> AsyncMock:
    """Set up an AsyncMock session for the dispatch handler's flow.

    ``recipient`` is returned from the locking ``select(...)``;
    ``message`` from the subsequent ``session.get(message_cls, ...)``.
    """
    locked_recipient = MagicMock()
    locked_recipient.scalar_one_or_none = MagicMock(return_value=recipient)

    session = AsyncMock()
    session.execute = AsyncMock(return_value=locked_recipient)
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=message)
    return session


def _last_execute_stmt(session: AsyncMock) -> Any:
    """Return the SQLAlchemy statement of the most recent execute call."""
    return session.execute.call_args_list[-1].args[0]


class TestMakeDispatchEntrypoint:
    @pytest.mark.asyncio
    async def test_marks_sent_on_transport_success(self):
        rid = uuid.uuid4()
        mid = uuid.uuid4()
        recipient = _StubRecipient(rid, mid, "email", "a@example.com")
        message = _StubMessage(mid)

        session = _stub_session(recipient=recipient, message=message)

        transport = LoggingTransport()

        handler = make_dispatch_entrypoint(
            session_factory=_stub_session_factory(session),
            transports={"email": transport},
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        await handler(_job(str(rid).encode("utf-8")))

        # Transport was called exactly once.
        assert len(transport.sent) == 1
        sent_mid, sent_addr, sent_body = transport.sent[0]
        assert sent_mid == mid
        assert sent_addr == "a@example.com"
        assert sent_body == "hello"

        # First execute is the locking SELECT, second is the UPDATE.
        assert session.execute.await_count == 2
        assert "'sent'" in compile_query(_last_execute_stmt(session))
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marks_failed_when_transport_raises(self):
        rid = uuid.uuid4()
        mid = uuid.uuid4()
        recipient = _StubRecipient(rid, mid, "email", "a@example.com")
        message = _StubMessage(mid)

        session = _stub_session(recipient=recipient, message=message)

        class Boom:
            async def send(self, *, message, recipient):  # noqa: ARG002
                msg = "smtp connection refused"
                raise RuntimeError(msg)

        handler = make_dispatch_entrypoint(
            session_factory=_stub_session_factory(session),
            transports={"email": Boom()},
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        await handler(_job(str(rid).encode("utf-8")))

        compiled = compile_query(_last_execute_stmt(session))
        assert "'failed'" in compiled
        assert "smtp connection refused" in compiled
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marks_failed_when_no_transport_for_method(self):
        rid = uuid.uuid4()
        mid = uuid.uuid4()
        recipient = _StubRecipient(rid, mid, "fax", "+1")
        message = _StubMessage(mid)

        session = _stub_session(recipient=recipient, message=message)

        handler = make_dispatch_entrypoint(
            session_factory=_stub_session_factory(session),
            transports={"email": LoggingTransport()},  # no "fax"
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        await handler(_job(str(rid).encode("utf-8")))

        compiled = compile_query(_last_execute_stmt(session))
        assert "'failed'" in compiled
        assert "no transport" in compiled

    @pytest.mark.asyncio
    async def test_locks_recipient_row_on_load(self):
        """Two workers can't both transition the same recipient to SENT.

        Concrete check: the recipient is loaded via
        ``select(...).with_for_update(skip_locked=True)`` so a
        concurrent worker holding the row sees no row and bails
        out before the transport runs.
        """
        rid = uuid.uuid4()
        mid = uuid.uuid4()
        recipient = _StubRecipient(rid, mid, "email", "a@example.com")
        message = _StubMessage(mid)

        session = _stub_session(recipient=recipient, message=message)

        handler = make_dispatch_entrypoint(
            session_factory=_stub_session_factory(session),
            transports={"email": LoggingTransport()},
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        await handler(_job(str(rid).encode("utf-8")))

        # First call to session.execute is the locking SELECT.
        # ``SKIP LOCKED`` is Postgres-specific so render against the
        # pg dialect to surface the modifier in the compiled SQL.
        select_stmt = session.execute.call_args_list[0].args[0]
        compiled = compile_query(select_stmt, dialect="postgres").upper()
        assert "FOR UPDATE" in compiled
        assert "SKIP LOCKED" in compiled

    @pytest.mark.asyncio
    async def test_skips_when_recipient_missing(self):
        rid = uuid.uuid4()
        session = _stub_session(recipient=None)

        handler = make_dispatch_entrypoint(
            session_factory=_stub_session_factory(session),
            transports={"email": LoggingTransport()},
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        await handler(_job(str(rid).encode("utf-8")))

        # Only the locking SELECT runs -- no UPDATE, no commit.
        assert session.execute.await_count == 1
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_status_already_terminal(self):
        rid = uuid.uuid4()
        mid = uuid.uuid4()
        recipient = _StubRecipient(
            rid,
            mid,
            "email",
            "a@example.com",
            status=DeliveryStatus.SENT.value,
        )

        session = _stub_session(recipient=recipient)

        handler = make_dispatch_entrypoint(
            session_factory=_stub_session_factory(session),
            transports={"email": LoggingTransport()},
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        await handler(_job(str(rid).encode("utf-8")))

        # Only the locking SELECT ran; no UPDATE, no commit.
        assert session.execute.await_count == 1
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_failed_when_message_missing(self):
        rid = uuid.uuid4()
        mid = uuid.uuid4()
        recipient = _StubRecipient(rid, mid, "email", "a@example.com")

        session = _stub_session(recipient=recipient, message=None)

        handler = make_dispatch_entrypoint(
            session_factory=_stub_session_factory(session),
            transports={"email": LoggingTransport()},
            message_cls=_Message,
            recipient_cls=_Recipient,
        )

        await handler(_job(str(rid).encode("utf-8")))

        compiled = compile_query(_last_execute_stmt(session))
        assert "message row missing" in compiled


# -------------------------------------------------------------------
# Misc helpers
# -------------------------------------------------------------------


class TestTruncate:
    def test_short_string_passes_through(self):
        assert _truncate("hello") == "hello"

    def test_long_string_clipped_with_ellipsis(self):
        s = "x" * 2000
        out = _truncate(s)
        assert len(out) == 1024
        assert out.endswith("…")


class TestLoadRecipients:
    @pytest.mark.asyncio
    async def test_returns_recipients_for_message(self):
        message_id = uuid.uuid4()
        rows = [
            _StubRecipient(uuid.uuid4(), message_id, "email", "a@example.com"),
            _StubRecipient(uuid.uuid4(), message_id, "sms", "+1"),
        ]

        scalars = MagicMock()
        scalars.__iter__ = MagicMock(return_value=iter(rows))
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)

        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)

        out = await load_recipients(session, _Recipient, message_id)
        assert out == rows

        # Issued the right where clause -- inspect the bound param
        # rather than literal-bind (UUIDs render without dashes when
        # bound, which makes substring assertions misleading).
        session.execute.assert_awaited_once()
        stmt = session.execute.call_args.args[0]
        params = stmt.compile().params
        assert params["message_id_1"] == message_id


# -------------------------------------------------------------------
# DISPATCH_ENTRYPOINT is a stable string constant
# -------------------------------------------------------------------


def test_dispatch_entrypoint_constant_is_string():
    assert isinstance(DISPATCH_ENTRYPOINT, str)
    assert DISPATCH_ENTRYPOINT == "ingot_comms_dispatch"


def test_module_exposes_protocol_aliases():
    # Smoke check that the public Protocol names are importable
    # from the module surface (caught by zuban anyway, but a runtime
    # assertion is cheap insurance against re-export drift).
    assert Transport is comms_mod.Transport
    assert Renderer is comms_mod.Renderer
    assert PreferenceResolver is comms_mod.PreferenceResolver
