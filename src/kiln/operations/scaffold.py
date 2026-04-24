"""Scaffold operations: db sessions and the auth token endpoint.

Produces :class:`~foundry.outputs.StaticFile` objects for
infrastructure files.  Split into two operations:

* :class:`Scaffold` -- always runs; emits the ``db/`` tree.
* :class:`AuthScaffold` -- runs only when the project config has
  ``auth`` set, via :meth:`AuthScaffold.when`.  Emits the generated
  ``POST {token_url}`` route (and, for cookie transport, a logout
  route).  The consumer's ``get_session`` dependency is *not*
  scaffolded; its dotted path is imported directly at use-sites.
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
    """Generate the token-issuance route.

    Emits a single :class:`StaticFile` at ``auth/router.py`` whose
    ``POST {token_url}`` handler parses the consumer's
    :attr:`~kiln.config.schema.AuthConfig.credentials_schema`,
    hands the instance to
    :attr:`~kiln.config.schema.AuthConfig.validate_fn`, and either
    returns an OAuth2-shaped JSON body (``type == "jwt"``) or sets
    an ``httpOnly`` cookie (``type == "cookie"``, which also gets
    a matching logout route).
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
        validate_module, validate_name = auth.validate_fn.rsplit(".", 1)
        yield StaticFile(
            path="auth/router.py",
            template="init/auth_router.py.j2",
            context={
                "creds_module": creds_module,
                "creds_name": creds_name,
                "validate_module": validate_module,
                "validate_name": validate_name,
                "transport": auth.type,
                "secret_env": auth.secret_env,
                "algorithm": auth.algorithm,
                "token_url": auth.token_url,
                "cookie_name": auth.cookie_name,
                "cookie_secure": auth.cookie_secure,
                "cookie_samesite": auth.cookie_samesite,
            },
        )
