Background tasks (pgqueuer)
===========================

.. contents:: On this page
   :local:
   :depth: 2

kiln-generated apps integrate with `pgqueuer
<https://github.com/janbjorge/pgqueuer>`_ — a PostgreSQL-backed job
queue that uses ``LISTEN`` / ``NOTIFY`` for low-latency dispatch.
The queue lives in the same database as your app, so producer-side
enqueue can join the request's transaction (transactional outbox)
and you don't run a separate broker.

kiln contributes two helpers in :mod:`ingot.queue` that bridge
SQLAlchemy and pgqueuer:

* :func:`ingot.queue.get_queue` — wraps a SQLAlchemy
  ``AsyncSession`` so the request can enqueue jobs inside its own
  transaction.
* :func:`ingot.queue.open_worker_driver` — opens an asyncpg
  connection from a DSN, coercing SQLAlchemy's
  ``postgresql+asyncpg://`` prefix to plain ``postgresql://`` so
  the same env var works for both halves.

Everything else (defining tasks, running the worker, tuning
entrypoints) is straight pgqueuer — kiln does not wrap, scaffold,
or rename pgqueuer's API.

Prerequisites
-------------

``pgqueuer[asyncpg]`` is already a runtime dep of ``kiln-generator``,
pulled in transitively when you install kiln.  No extra install is
needed.

You need PostgreSQL 9.5 or newer (any version with ``LISTEN`` /
``NOTIFY``).

Schema setup
------------

pgqueuer ships its own schema: one table for jobs, one for stats,
plus an enum, an index, and a notification trigger.  The schema is
**not** part of your alembic chain — pgqueuer's CLI owns it.

Run the install once per environment, before the worker first
starts:

.. code-block:: shell

   pgq install --pg-dsn "$DATABASE_URL"

If you upgrade pgqueuer and the new version ships schema changes,
re-run ``pgq install``.  Uninstall with
``pgq uninstall --pg-dsn ...``.

.. note::

   The DSN passed to ``pgq install`` should be a plain
   ``postgresql://...`` URL.  If your env var is SQLAlchemy-shaped
   (``postgresql+asyncpg://...``), strip the ``+asyncpg`` first or
   point ``pgq install`` at a separate plain-DSN env var.

Defining tasks
--------------

A pgqueuer task is just an async function taking a
:class:`pgqueuer.Job`.  Put them anywhere importable:

.. code-block:: python

   # blog/queue/tasks.py
   from pgqueuer import Job


   async def index_article(job: Job) -> None:
       article_id = job.payload.decode()
       # ... look up the article, push to search index, etc.


   async def send_welcome(job: Job) -> None:
       user_id = job.payload.decode()
       # ... send the email

``job.payload`` is ``bytes | None``.  JSON-encode if you need
structured data: ``json.dumps({"id": ...}).encode()``.

Worker factory
--------------

The factory creates a :class:`pgqueuer.PgQueuer`, registers each
entrypoint, and returns it.  Use
:func:`ingot.queue.open_worker_driver` to open the connection — it
strips the ``+asyncpg`` prefix so the same ``DATABASE_URL`` env var
works for both the request path and the worker.

.. code-block:: python

   # blog/queue/main.py
   import os
   from datetime import timedelta

   from pgqueuer import PgQueuer

   from ingot.queue import open_worker_driver

   from blog.queue.tasks import index_article, send_welcome


   async def main() -> PgQueuer:
       """Worker factory invoked by ``pgq run blog.queue.main:main``."""
       async with open_worker_driver(os.environ["DATABASE_URL"]) as driver:
           pgq = PgQueuer(driver)

           pgq.entrypoint("index_article", concurrency_limit=4)(
               index_article
           )
           pgq.entrypoint(
               "send_welcome",
               concurrency_limit=2,
               requests_per_second=10.0,
               retry_timer=timedelta(seconds=30),
           )(send_welcome)

           return pgq

You can also use ``@pgq.entrypoint(...)`` as a decorator inline:

.. code-block:: python

   async def main() -> PgQueuer:
       async with open_worker_driver(os.environ["DATABASE_URL"]) as driver:
           pgq = PgQueuer(driver)

           @pgq.entrypoint("index_article", concurrency_limit=4)
           async def index_article(job): ...

           return pgq

Available ``entrypoint`` kwargs (from pgqueuer):

* ``concurrency_limit: int`` — max parallel jobs (default ``0`` =
  unlimited).
* ``requests_per_second: float`` — token-bucket rate limit (default
  ``inf``).
* ``retry_timer: timedelta`` — re-queue jobs stuck in-progress
  longer than this (default ``timedelta(0)`` = no retry).
* ``serialized_dispatch: bool`` — force sequential per
  ``dedupe_key`` (default ``False``).

Running the worker
------------------

pgqueuer ships its own CLI; nothing kiln-specific:

.. code-block:: shell

   pgq run blog.queue.main:main

Long-running.  Run it as a service / pod / supervised process.

To split work across pod classes — e.g. a small fast pool and a
larger slow pool — write multiple factories and point each
deployment at the right one:

.. code-block:: shell

   # pod class A
   pgq run blog.queue.fast:main

   # pod class B
   pgq run blog.queue.slow:main

Both pods share one binary; only the entrypoints registered in the
factory each pod uses are pulled.

Enqueueing from a request
-------------------------

This is where kiln's :func:`~ingot.queue.get_queue` shines.  It
returns a :class:`pgqueuer.Queries` bound to the asyncpg connection
underlying your SQLAlchemy session — so ``enqueue`` runs in the
**same transaction** as your other writes.  If the request commits,
the job is durable; if it rolls back, the job never existed.

In an action body (kiln-generated routes pass ``session``
automatically):

.. code-block:: python

   # blog/actions.py
   from uuid import UUID

   from sqlalchemy.ext.asyncio import AsyncSession

   from ingot.queue import get_queue


   async def publish(article_id: UUID, session: AsyncSession) -> None:
       # ... whatever business logic updates the article row ...
       article = await session.get(Article, article_id)
       article.status = "published"

       queue = await get_queue(session)
       await queue.enqueue(
           ["index_article"],
           [str(article_id).encode()],
       )
       # The session commit (handled by kiln) makes both the row
       # update AND the job insert durable atomically.  Roll back
       # and neither one happened.

:meth:`pgqueuer.Queries.enqueue` accepts:

.. code-block:: python

   await queue.enqueue(
       entrypoint,         # str or list[str] — task name(s)
       payload,            # bytes | None or list[bytes | None]
       priority=0,         # int or list[int] — higher runs first
       execute_after=None, # timedelta — delay job start
       dedupe_key=None,    # str — drop duplicates within window
       headers=None,       # dict[str, str] — arbitrary metadata
   )

Pass lists to enqueue many jobs at once in a single round-trip.

Testing
-------

Tasks are plain async functions; test them directly with a fake
:class:`~pgqueuer.Job`:

.. code-block:: python

   from datetime import datetime, UTC
   from uuid import uuid4

   import pytest
   from pgqueuer import Job

   from blog.queue.tasks import index_article


   @pytest.mark.asyncio
   async def test_index_article():
       article_id = uuid4()
       fake_job = Job(
           id=1,
           priority=0,
           created=datetime.now(UTC),
           updated=datetime.now(UTC),
           status="picked",
           entrypoint="index_article",
           payload=str(article_id).encode(),
           queue_manager_id=uuid4(),
       )
       await index_article(fake_job)
       # ... assertions

For end-to-end tests of enqueue-from-request, use a real Postgres
(testcontainers / docker-compose).  ``get_queue`` needs a live
asyncpg connection underlying the session.

Common pitfalls
---------------

SQLAlchemy URL prefix
^^^^^^^^^^^^^^^^^^^^^

Your ``DATABASE_URL`` env var probably starts with
``postgresql+asyncpg://`` because that's what SQLAlchemy wants.
:func:`~ingot.queue.open_worker_driver` strips the ``+asyncpg``
automatically.  If you bypass it and call ``asyncpg.connect``
yourself, strip the prefix first or asyncpg will reject the DSN.

Worker connection ≠ request pool
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The worker needs its **own** long-lived connection so pgqueuer can
``LISTEN`` on it.  Don't try to share the SQLAlchemy pool — you
will lose notifications.  :func:`~ingot.queue.open_worker_driver`
opens a fresh connection for exactly this reason.

Session not yet checked out
^^^^^^^^^^^^^^^^^^^^^^^^^^^

:func:`~ingot.queue.get_queue` walks
``session.connection() → get_raw_connection() → driver_connection``.
If the session has never been used, the underlying driver
connection may be ``None`` and ``get_queue`` raises
:class:`RuntimeError`.  Doing any read first (or
``await session.connection()``) before enqueue is enough.

``payload`` is bytes
^^^^^^^^^^^^^^^^^^^^

Not ``dict``, not ``str``.  JSON-encode if you need structured
data: ``payload=json.dumps({"id": ...}).encode()``.

Forgot ``pgq install``
^^^^^^^^^^^^^^^^^^^^^^

Worker will start and then immediately error trying to query a
missing table.  Run the install once per fresh database (and again
after upgrading pgqueuer).

Schema migrations
^^^^^^^^^^^^^^^^^

kiln's alembic chain doesn't manage pgqueuer's tables.  Don't try
to put them in there — let pgqueuer's CLI own the schema.  This
is the pattern recommended by pgqueuer itself.
