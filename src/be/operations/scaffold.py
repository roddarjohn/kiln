"""Scaffold operations: db sessions and the auth package.

Produces :class:`~foundry.outputs.StaticFile` objects for
infrastructure files.  Split into two operations:

* :class:`Scaffold` -- always runs; emits the ``db/`` tree.
* :class:`AuthScaffold` -- runs only when the project config has
  ``auth`` set, via :meth:`AuthScaffold.when`.  Emits the
  session-dep and login/logout routes (three files under
  ``auth/``); the consumer provides only the session/credentials
  schemas and the credential-validation function.

Pgqueuer integration is intentionally *not* scaffolded -- users
write their own ``main()`` factory per pgqueuer's CLI idiom and
run ``pgq run module:main``.  Kiln contributes only the
runtime helpers in :mod:`ingot.queue` (``get_queue``,
``open_worker_driver``).
"""

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig
    from foundry.engine import BuildContext


@operation("scaffold", scope="project")
class Scaffold:
    """Generate ``db/`` infrastructure files."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce static files for db sessions.

        Args:
            ctx: Build context with project config.
            _options: Unused (no options).

        Yields:
            :class:`~foundry.outputs.StaticFile` objects for the
            ``db/`` package and one session module per configured
            database.

        """
        config = ctx.instance
        instrument_sqlalchemy = (
            config.telemetry is not None
            and config.telemetry.instrument_sqlalchemy
        )

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
                    "instrument_sqlalchemy": instrument_sqlalchemy,
                },
            )


@operation("auth_scaffold", scope="project")
class AuthScaffold:
    """Generate the ``auth/`` package.

    Emits three :class:`~foundry.outputs.StaticFile` objects:

    * ``auth/__init__.py`` -- package marker.
    * ``auth/dependencies.py`` -- binds :func:`ingot.auth.session_auth`
      against the consumer's
      :attr:`~be.config.schema.AuthConfig.session_schema` to
      produce the ``get_session`` FastAPI dependency used by every
      protected route.
    * ``auth/router.py`` -- login (``POST {token_url}``) and logout
      (``POST {token_url}/logout``) handlers that call
      :func:`ingot.auth.issue_session` /
      :func:`~ingot.auth.clear_session`.
    """

    def when(self, ctx: BuildContext[ProjectConfig, ProjectConfig]) -> bool:
        """Apply only when the project config has ``auth`` set.

        Args:
            ctx: Build context with project config.

        Returns:
            ``True`` when ``ctx.instance.auth`` is not ``None``.

        """
        return bool(ctx.instance.auth)

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the auth router static file.

        Args:
            ctx: Build context with project config.  ``when`` has
                already confirmed ``ctx.instance.auth is not None``.
            _options: Unused (no options).

        Yields:
            A single :class:`~foundry.outputs.StaticFile` for
            ``auth/router.py``.

        """
        auth = ctx.instance.auth
        assert auth is not None  # noqa: S101 -- guaranteed by when()

        has_telemetry = bool(ctx.instance.telemetry)

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
                "has_telemetry": has_telemetry,
            },
        )
