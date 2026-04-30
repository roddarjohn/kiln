"""Runtime helpers for kiln-generated FastAPI projects.

Each submodule groups related primitives:

* :mod:`ingot.auth` -- JWT session auth (bearer + cookie transports).
* :mod:`ingot.comms` -- communication-platform primitives:
  :class:`~ingot.comms.CommType` registry, transport adapters,
  preference resolver, and a transactional-outbox
  :func:`~ingot.comms.send_communication` that rides the request
  session via pgqueuer.
* :mod:`ingot.files` -- :class:`~ingot.files.FileMixin` (pgcraft-
  flavoured storage-column mixin) + S3 client + four ready-made
  action functions.  Requires the ``files`` extra
  (``pip install 'kiln-generator[files]'``) for ``boto3``.
* :mod:`ingot.filters` -- declarative filter expressions.
* :mod:`ingot.ordering` -- sort-direction + apply-ordering helper.
* :mod:`ingot.pagination` -- keyset and offset pagination.
* :mod:`ingot.queue` -- pgqueuer integration: :func:`~ingot.queue.get_queue`
  for transactional-outbox enqueue from a SQLAlchemy session, and
  :func:`~ingot.queue.open_worker_driver` for the worker-side
  asyncpg connection.
* :mod:`ingot.utils` -- ``get_object_from_query_or_404`` (the
  load-or-404 helper used by every read/mutate handler) and
  :func:`~ingot.utils.run_once`.

Generated code imports from the owning submodule directly --
``from ingot.files import FileMixin``,
``from ingot.auth import session_auth`` -- so the package root is
intentionally empty.  This keeps the public surface organized by
concern rather than as one flat namespace.

Everything here is pure Python -- the be CLI knows to emit
imports pointing at these submodules instead of scaffolding a
``utils.py`` into the generated app.
"""
