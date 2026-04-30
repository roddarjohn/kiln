Communication platform
======================

.. contents:: On this page
   :local:
   :depth: 2

kiln-generated apps can ship a typed, durable communication
platform: declare your *communication types* (with a Pydantic
context schema and templates), point at your transports
(SMTP, Twilio, FCM, ...), and the generator wires up a registry,
a producer wrapper, and a pgqueuer-backed worker dispatch handler.
The platform reuses the same transactional-outbox bridge described
in :doc:`pgqueuer` -- a send is durable iff the request transaction
commits.

The platform is fully **opt-in**.  Without ``project.comms`` set,
the generator emits zero references to it; the runtime cost is
exactly zero.

No HTTP routes are emitted.  Triggering a send is your concern --
typically inside an action handler, a webhook receiver, or a
scheduled job.  See :ref:`comms-sending`.

What you get
------------

The generator produces ``_generated/comms.py`` exposing three
symbols:

``registry``
    A populated :class:`ingot.comms.CommRegistry` -- one
    :class:`~ingot.comms.CommType` per declared entry.

``send_comm(...)``
    Thin wrapper over :func:`ingot.comms.send_communication` with
    your message / recipient ORM classes and the registry pre-bound.
    Call it from any request handler.

``dispatch``
    A pre-built worker handler from
    :func:`ingot.comms.make_dispatch_entrypoint`.  Register it
    against :data:`ingot.comms.DISPATCH_ENTRYPOINT` on your pgqueuer
    instance; the handler routes each job to the right transport
    and stamps success / failure on the recipient row.

The runtime helpers (mixins, transport / preference / renderer
protocols, the producer + dispatch builders) live in
:mod:`ingot.comms`.

Enabling
--------

Add a ``comms`` block to your project config using the
``be/comms/comms.libsonnet`` helper:

.. code-block:: jsonnet

   local comms = import 'be/comms/comms.libsonnet';
   local db = import 'be/db/databases.libsonnet';

   {
     databases: [db.postgres('primary', { default: true })],

     comms: comms.platform({
       message_model:   'myapp.models.CommMessage',
       recipient_model: 'myapp.models.CommRecipient',
       transports: {
         email: 'myapp.comms.transports.email_transport',
         sms:   'myapp.comms.transports.sms_transport',
       },
       preferences: 'myapp.comms.prefs.resolver',  // optional

       types: [
         comms.type({
           name: 'order_shipped',
           context_schema: 'myapp.comms.contexts.OrderShipped',
           subject_template: 'Order {{ order_id }} shipped',
           body_template: comms.path('templates/order_shipped.html'),
           default_methods: ['email', 'sms'],
         }),
       ],
     }),

     apps: [...],
   }

The full schema lives in :class:`be.config.schema.CommsConfig` and
:class:`be.config.schema.CommTypeConfig`.

Models
------

You own the three tables; ingot owns the columns.  Same idiom as
:class:`ingot.files.FileMixin` and
:class:`ingot.rate_limit.RateLimitBucketMixin`.

.. code-block:: python

   # myapp/models.py
   import uuid
   from sqlalchemy import ForeignKey
   from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

   from ingot.comms import (
       MessageMixin,
       NotificationPreferenceMixin,
       RecipientMixin,
   )


   class Base(DeclarativeBase):
       pass


   class CommMessage(Base, MessageMixin):
       __tablename__ = 'comm_messages'


   class CommRecipient(Base, RecipientMixin):
       __tablename__ = 'comm_recipients'

       # The mixin leaves message_id FK-free so it works against
       # any message table name; bolt on the FK in your subclass.
       message_id: Mapped[uuid.UUID] = mapped_column(
           ForeignKey('comm_messages.id', ondelete='CASCADE'),
           nullable=False,
           index=True,
       )


   class NotificationPreference(Base, NotificationPreferenceMixin):
       __tablename__ = 'comm_preferences'

Migrations are yours -- be doesn't generate alembic.  Run
``alembic revision --autogenerate`` against your ``Base.metadata``;
all three tables come out as standard ``CREATE TABLE``.

What's stored:

* :class:`~ingot.comms.MessageMixin` -- the *intent*.  ``comm_type``,
  the JSON-dumped context, the rendered ``subject``/``body``,
  ``created_at``.  Storing the rendered output (not just the
  context) means template churn doesn't invalidate the audit log.
* :class:`~ingot.comms.RecipientMixin` -- one row per
  ``(message, method, address)``.  Carries ``status``, ``sent_at``,
  ``error``.  The pgqueuer job payload is the recipient's id.
* :class:`~ingot.comms.NotificationPreferenceMixin` --
  ``(subject_key, comm_type, method) -> enabled``.  Looked up by
  your :class:`~ingot.comms.PreferenceResolver` implementation; the
  mixin only supplies the columns.

Comm types
----------

A comm type binds a name (the registry key) to:

* A Pydantic context schema -- validated before render.
* A subject template and a body template.
* A list of *default methods* (``email``, ``sms``, ...) -- a
  documentation hint today; the platform always uses
  caller-supplied recipients.

.. code-block:: python

   # myapp/comms/contexts.py
   from pydantic import BaseModel


   class OrderShipped(BaseModel):
       order_id: str
       customer_name: str


   class PasswordReset(BaseModel):
       reset_link: str

The ``context_schema`` field in the jsonnet config is a dotted
import path to one of these classes.  The generator wires the import
into ``_generated/comms.py``; you don't import them yourself.

Templates: inline or file
-------------------------

``subject_template`` and ``body_template`` accept either an inline
string or a file reference:

.. code-block:: jsonnet

   // Inline (most common -- short subjects, SMS bodies).
   subject_template: 'Order {{ order_id }} shipped',

   // File-backed (HTML email bodies, MJML, anything multi-line).
   body_template: comms.path('templates/order_shipped.html'),

Resolution semantics for ``comms.path(...)``:

1. Path is interpreted relative to the directory in which
   ``foundry generate`` is invoked (or absolute).
2. The file is read **once at build time**.
3. Its contents are inlined into ``_generated/comms.py`` as a
   string literal.

The generated tree carries no runtime file dependency.  Re-running
``foundry generate`` is the only way template-file edits propagate.
A missing file raises :class:`FileNotFoundError` with the comm-type
name and the offending field (``subject`` vs ``body``) in the
message.

Transports
----------

A transport is a small adapter implementing the
:class:`~ingot.comms.Transport` protocol -- one ``async send``
method that takes the message + recipient rows and either delivers
or raises.

.. code-block:: python

   # myapp/comms/transports.py
   import smtplib
   from email.message import EmailMessage

   from ingot.comms import MessageMixin, RecipientMixin


   class SmtpTransport:
       def __init__(self, host: str, sender: str) -> None:
           self._host = host
           self._sender = sender

       async def send(
           self,
           *,
           message: MessageMixin,
           recipient: RecipientMixin,
       ) -> None:
           msg = EmailMessage()
           msg['From'] = self._sender
           msg['To'] = recipient.address
           msg['Subject'] = message.subject or ''
           msg.set_content(message.body)
           with smtplib.SMTP(self._host) as smtp:
               smtp.send_message(msg)


   # Module-level instance -- the dotted path in project.jsonnet
   # points here.
   email_transport = SmtpTransport(
       host='smtp.internal',
       sender='noreply@example.com',
   )

The dotted paths in the ``transports`` map of your config resolve
to *instances*, not classes -- you construct them once at
import time.

For tests and local development, ingot ships
:class:`~ingot.comms.LoggingTransport` -- it appends every send
into an in-memory list you can assert against.

Renderer
--------

By default the platform uses the in-process
:class:`~ingot.comms.JinjaRenderer` (Jinja2 against the validated
context dump).  Swap it out by setting the optional ``renderer``
field in your config:

.. code-block:: jsonnet

   comms: comms.platform({
     ...,
     renderer: 'myapp.comms.renderers.node_renderer',
   })

The dotted path resolves to an instance implementing the
single-method :class:`~ingot.comms.Renderer` protocol:

.. code-block:: python

   from ingot.comms import CommType, RenderedMessage
   from pydantic import BaseModel


   class NodeRenderer:
       """HTTP-call into a separate Node template service."""

       async def render(
           self,
           comm_type: CommType,
           context: BaseModel,
       ) -> RenderedMessage:
           # POST context to your renderer service, return its result.
           ...

The same shape fits any out-of-process renderer (MJML compiler,
React-email service, ...).  Producer-side code never changes;
only the configured instance.

Preferences
-----------

Per-recipient opt-in is implemented behind the
:class:`~ingot.comms.PreferenceResolver` protocol.  The platform
calls the resolver once per recipient that has a non-``None``
``subject_key``; recipients with ``subject_key=None`` skip the
check entirely (typical for non-user addresses like a billing
inbox).

A minimal database-backed resolver:

.. code-block:: python

   # myapp/comms/prefs.py
   from sqlalchemy import select

   from myapp.db import SessionLocal
   from myapp.models import NotificationPreference


   class DbPreferenceResolver:
       async def is_enabled(
           self,
           *,
           subject_key: str,
           comm_type: str,
           method: str,
       ) -> bool:
           async with SessionLocal() as session:
               row = (
                   await session.execute(
                       select(NotificationPreference.enabled).where(
                           NotificationPreference.subject_key == subject_key,
                           NotificationPreference.comm_type == comm_type,
                           NotificationPreference.method == method,
                       )
                   )
               ).scalar_one_or_none()
           # Absent row defaults to opt-in.
           return True if row is None else bool(row)


   resolver = DbPreferenceResolver()

An opted-out recipient yields **no** ``RecipientMixin`` row and
**no** pgqueuer job.  The :class:`~ingot.comms.MessageMixin` row
still records the attempt so the audit trail is honest.

.. _comms-sending:

Sending
-------

``send_comm`` runs inside the request handler's transaction.  The
single ``await`` does five things:

1. Validate ``context`` against the comm type's
   ``context_schema``.
2. Render the templates with the configured renderer.
3. Insert the :class:`~ingot.comms.MessageMixin` row.
4. For each recipient, consult the
   :class:`~ingot.comms.PreferenceResolver` (if configured) and
   insert a :class:`~ingot.comms.RecipientMixin` row for each one
   that passes.
5. Enqueue one pgqueuer job per surviving recipient under
   :data:`ingot.comms.DISPATCH_ENTRYPOINT`, payload = recipient
   id.

Everything rides the request session's transaction.  When the
request commits, the message row, recipient rows, and pgqueuer
jobs all become durable atomically.  Roll back and the comm never
happened.

.. code-block:: python

   from sqlalchemy.ext.asyncio import AsyncSession

   from ingot.comms import RecipientSpec
   from ingot.queue import get_queue
   from myapp.comms.contexts import OrderShipped
   from _generated.comms import send_comm


   async def notify_shipped(order, *, db: AsyncSession) -> None:
       queue = await get_queue(db)  # rides the same transaction

       await send_comm(
           session=db,
           queue=queue,
           comm_type='order_shipped',
           context=OrderShipped(
               order_id=order.id,
               customer_name=order.customer.name,
           ),
           recipients=[
               RecipientSpec(
                   method='email',
                   address=order.customer.email,
                   subject_key=str(order.customer.id),  # opens prefs
               ),
               RecipientSpec(
                   method='sms',
                   address=order.customer.phone,
                   subject_key=str(order.customer.id),
               ),
           ],
       )

The ``context`` argument also accepts a plain dict -- the registry
validates it against the declared Pydantic schema before rendering.

Worker
------

Register the generated ``dispatch`` handler against
:data:`ingot.comms.DISPATCH_ENTRYPOINT` in your pgqueuer worker
factory.  Any other entrypoints you have go alongside it -- the
comms handler is just one more entry in the same factory.

.. code-block:: python

   # myapp/worker.py
   import os

   from pgqueuer import PgQueuer

   from ingot.comms import DISPATCH_ENTRYPOINT
   from ingot.queue import open_worker_driver

   from _generated.comms import dispatch


   async def main() -> PgQueuer:
       async with open_worker_driver(os.environ['DATABASE_URL']) as driver:
           pgq = PgQueuer(driver)
           pgq.entrypoint(DISPATCH_ENTRYPOINT)(dispatch)
           return pgq

Run with ``pgq run myapp.worker:main`` (see :doc:`pgqueuer` for the
full worker story including ``pgq install``, retry, concurrency).

The dispatch handler:

1. Decodes the recipient id from the job payload.
2. Loads the recipient + message rows.
3. Skips if the recipient is missing or already past
   :attr:`~ingot.comms.DeliveryStatus.PENDING` (re-fire idempotency).
4. Looks up the configured transport for the recipient's method;
   marks the row failed if no transport is registered for that
   method.
5. Calls ``transport.send``.  Stamps ``status='sent'`` +
   ``sent_at`` on success, or ``status='failed'`` + truncated
   ``error`` on raise.

The audit trail lives on the row.  No ad-hoc logging is required.

Lifecycle in one diagram
------------------------

.. code-block:: text

   request handler
     -> send_comm(session, queue, ...)
          -> validate(context)
          -> render(subject, body)
          -> INSERT comm_messages
          -> for recipient in recipients:
               -> [preference check]
               -> INSERT comm_recipients
               -> queue.enqueue(DISPATCH_ENTRYPOINT, recipient.id)
     -> session.commit()        ← atomic; nothing has been *sent* yet

   pgqueuer notifies the worker
     -> dispatch(job)
          -> load recipient + message
          -> transport.send(...)
          -> UPDATE comm_recipients SET status, sent_at | error

Pitfalls
--------

Worker can't share the request connection
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The generated ``dispatch`` handler opens a short-lived session per
job from the project's ``async_sessionmaker``.  The producer side
(``send_comm``) rides the request's session.  Don't try to reuse
the request session inside the worker -- it's gone by then.

``payload`` is bytes
^^^^^^^^^^^^^^^^^^^^

The platform handles encoding internally
(``str(uuid).encode("utf-8")``).  You don't construct payloads
yourself; you pass :class:`~ingot.comms.RecipientSpec` instances.

Method names match transport keys
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A :class:`~ingot.comms.RecipientSpec` with ``method='fax'`` requires
a transport keyed ``'fax'`` in your config.  Mismatches are caught
at dispatch time, not config time -- the recipient row gets stamped
``status='failed'`` with ``error='no transport for method ...'``.
This is intentional: a comm-type's
:attr:`~be.config.schema.CommTypeConfig.default_methods` is just
a hint, and you may want to add new methods independently of the
comm-type registry.

Re-rendering
^^^^^^^^^^^^

The :class:`~ingot.comms.MessageMixin` row stores the *rendered*
subject and body.  If you change a template, the audit log
continues to reflect what the recipient actually saw.  To re-send
with the new template, send a new comm.

Schema migrations
^^^^^^^^^^^^^^^^^

The mixins supply columns; you own the table.  Run alembic against
your ``Base.metadata`` as usual.  The columns themselves don't
churn between kiln releases unless explicitly noted in the
changelog -- the platform's contract is the columns, not the
table.

Forgot ``pgq install``
^^^^^^^^^^^^^^^^^^^^^^

The dispatch worker is just another pgqueuer worker.  ``pgq
install`` is required once per environment for the queue to
work at all -- see :doc:`pgqueuer`.
