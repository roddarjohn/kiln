"""Scaffold operations: db sessions, auth, and pgqueuer wiring.

Produces :class:`~foundry.outputs.StaticFile` objects for
infrastructure files.  Split into three operations:

* :class:`Scaffold` -- always runs; emits the ``db/`` tree.
* :class:`AuthScaffold` -- runs only when the project config has
  ``auth`` set, via :meth:`AuthScaffold.when`.  Emits the
  session-dep and login/logout routes (three files under
  ``auth/``); the consumer provides only the session/credentials
  schemas and the credential-validation function.
* :class:`QueueScaffold` -- runs only when the project config has
  ``queue`` set, via :meth:`QueueScaffold.when`.  Emits the
  ``queue/`` package with a worker entrypoint and a task
  registry; the consumer writes the task bodies and enqueues
  jobs from action handlers via :func:`ingot.get_queue`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from kiln.config.schema import ProjectConfig


@operation("scaffold", scope="project")
class Scaffold:
    """Generate ``db/`` infrastructure files."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce static files for db sessions.

        Args:
            ctx: Build context with project config.
            _options: Unused (no options).

        Yields:
            :class:`StaticFile` objects for the ``db/`` package and
            one session module per configured database.

        """
        config = ctx.instance

        yield StaticFile(
            path="db/__init__.py",
            template="",
            context={},
        )

        for db in config.databases:
            yield StaticFile(
                path=f"db/{db.key}_session.py",
                template="init/db_session.py.j2",
                context={
                    "key": db.key,
                    "url_env": db.url_env,
                    "echo": db.echo,
                    "pool_size": db.pool_size,
                    "max_overflow": db.max_overflow,
                    "pool_timeout": db.pool_timeout,
                    "pool_recycle": db.pool_recycle,
                    "pool_pre_ping": db.pool_pre_ping,
                    "get_db_fn": f"get_{db.key}_db",
                },
            )


@operation("auth_scaffold", scope="project")
class AuthScaffold:
    """Generate the ``auth/`` package.

    Emits three :class:`StaticFile` objects:

    * ``auth/__init__.py`` -- package marker.
    * ``auth/dependencies.py`` -- binds :func:`ingot.auth.session_auth`
      against the consumer's :attr:`session_schema` to produce the
      ``get_session`` FastAPI dependency used by every protected
      route.
    * ``auth/router.py`` -- login (``POST {token_url}``) and logout
      (``POST {token_url}/logout``) handlers that call
      :func:`ingot.auth.issue_session` / :func:`clear_session`.
    """

    def when(self, ctx: BuildContext[ProjectConfig]) -> bool:
        """Apply only when the project config has ``auth`` set.

        Args:
            ctx: Build context with project config.

        Returns:
            ``True`` when ``ctx.instance.auth`` is not ``None``.

        """
        return ctx.instance.auth is not None

    def build(
        self,
        ctx: BuildContext[ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the auth router static file.

        Args:
            ctx: Build context with project config.  ``when`` has
                already confirmed ``ctx.instance.auth is not None``.
            _options: Unused (no options).

        Yields:
            A single :class:`StaticFile` for ``auth/router.py``.

        """
        auth = ctx.instance.auth
        assert auth is not None  # noqa: S101 -- guaranteed by when()

        yield StaticFile(
            path="auth/__init__.py",
            template="",
            context={},
        )

        creds_module, creds_name = auth.credentials_schema.rsplit(".", 1)
        session_module, session_name = auth.session_schema.rsplit(".", 1)
        validate_module, validate_name = auth.validate_fn.rsplit(".", 1)

        store_module: str | None = None
        store_name: str | None = None
        if auth.session_store is not None:
            store_module, store_name = auth.session_store.rsplit(".", 1)

        yield StaticFile(
            path="auth/dependencies.py",
            template="init/auth_dependencies.py.j2",
            context={
                "session_module": session_module,
                "session_name": session_name,
                "sources": list(auth.sources),
                "secret_env": auth.secret_env,
                "algorithm": auth.algorithm,
                "token_url": auth.token_url,
                "cookie_name": auth.cookie_name,
                "store_module": store_module,
                "store_name": store_name,
            },
        )

        yield StaticFile(
            path="auth/router.py",
            template="init/auth_router.py.j2",
            context={
                "creds_module": creds_module,
                "creds_name": creds_name,
                "session_module": session_module,
                "session_name": session_name,
                "validate_module": validate_module,
                "validate_name": validate_name,
                "sources": list(auth.sources),
                "secret_env": auth.secret_env,
                "algorithm": auth.algorithm,
                "token_url": auth.token_url,
                "cookie_name": auth.cookie_name,
                "cookie_secure": auth.cookie_secure,
                "cookie_samesite": auth.cookie_samesite,
                "store_module": store_module,
                "store_name": store_name,
            },
        )


@operation("queue_scaffold", scope="project")
class QueueScaffold:
    """Generate the ``queue/`` package for pgqueuer integration.

    Emits three :class:`StaticFile` objects:

    * ``queue/__init__.py`` -- package marker.
    * ``queue/tasks.py`` -- imports each user task fn and exposes
      a ``TASKS`` dict mapping pgqueuer entrypoint name → fn.
    * ``queue/worker.py`` -- ``python -m`` entrypoint that opens
      a dedicated asyncpg connection (via
      :func:`ingot.open_worker_driver`), instantiates a
      :class:`pgqueuer.PgQueuer`, registers every task in
      ``TASKS`` as an entrypoint, and runs.

    Producers (request-side enqueue) are not generated -- users
    call :func:`ingot.get_queue` from inside action bodies when
    they want to enqueue a job inside the request's transaction.
    """

    def when(self, ctx: BuildContext[ProjectConfig]) -> bool:
        """Apply only when the project config has ``queue`` set.

        Args:
            ctx: Build context with project config.

        Returns:
            ``True`` when ``ctx.instance.queue`` is not ``None``.

        """
        return ctx.instance.queue is not None

    def build(
        self,
        ctx: BuildContext[ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the queue package static files.

        Args:
            ctx: Build context with project config.  ``when`` has
                already confirmed ``ctx.instance.queue is not None``.
            _options: Unused (no options).

        Yields:
            Three :class:`StaticFile` objects under ``queue/``.

        """
        config = ctx.instance
        queue = config.queue
        assert queue is not None  # noqa: S101 -- guaranteed by when()

        database = config.resolve_database(queue.database)

        tasks = [
            {
                "name": task.name,
                "module": task.fn.rsplit(".", 1)[0],
                "fn_name": task.fn.rsplit(".", 1)[1],
            }
            for task in queue.tasks
        ]

        yield StaticFile(
            path="queue/__init__.py",
            template="",
            context={},
        )

        yield StaticFile(
            path="queue/tasks.py",
            template="init/queue_tasks.py.j2",
            context={"tasks": tasks},
        )

        worker_module = (
            f"{config.package_prefix}.queue.worker"
            if config.package_prefix
            else "queue.worker"
        )

        yield StaticFile(
            path="queue/worker.py",
            template="init/queue_worker.py.j2",
            context={
                "url_env": database.url_env,
                "db_key": database.key,
                "worker_module": worker_module,
            },
        )
