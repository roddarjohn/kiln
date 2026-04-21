"""Scaffold operation: db sessions and auth dependencies.

Produces :class:`~foundry.outputs.StaticFile` objects for
infrastructure files (database sessions, authentication).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext


@operation("scaffold", scope="project")
class Scaffold:
    """Generate ``db/`` and ``auth/`` infrastructure files."""

    def build(
        self,
        ctx: BuildContext,
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce static files for db sessions and auth.

        Args:
            ctx: Build context with project config.
            _options: Unused (no options).

        Yields:
            :class:`StaticFile` objects for db and auth infrastructure.

        """
        config = ctx.config

        yield StaticFile(
            path="db/__init__.py",
            template="",
            context={},
        )

        databases = getattr(config, "databases", [])
        if databases:
            for db in databases:
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
        else:
            yield StaticFile(
                path="db/session.py",
                template="init/db_session.py.j2",
                context={
                    "key": None,
                    "url_env": "DATABASE_URL",
                    "echo": False,
                    "pool_size": 5,
                    "max_overflow": 10,
                    "pool_timeout": 30,
                    "pool_recycle": -1,
                    "pool_pre_ping": True,
                    "get_db_fn": "get_db",
                },
            )

        auth = getattr(config, "auth", None)
        if auth is not None:
            yield StaticFile(
                path="auth/__init__.py",
                template="",
                context={},
            )
            yield _auth_deps_static(auth)
            if auth.get_current_user_fn is None:
                yield _auth_router_static(auth)


def _auth_deps_static(auth: object) -> StaticFile:
    """Build the auth/dependencies.py static file."""
    gcu_fn = getattr(auth, "get_current_user_fn", None)
    gcu_module = None
    gcu_name = None
    if gcu_fn:
        gcu_module, gcu_name = gcu_fn.rsplit(".", 1)
    return StaticFile(
        path="auth/dependencies.py",
        template="init/auth_dependencies.py.j2",
        context={
            "gcu_module": gcu_module,
            "gcu_name": gcu_name,
            "secret_env": getattr(auth, "secret_env", "JWT_SECRET"),
            "algorithm": getattr(auth, "algorithm", "HS256"),
            "token_url": getattr(auth, "token_url", "/auth/token"),
        },
    )


def _auth_router_static(auth: object) -> StaticFile:
    """Build the auth/router.py static file."""
    vcf = getattr(auth, "verify_credentials_fn", None)
    if vcf is None:  # pragma: no cover
        msg = "verify_credentials_fn is required"
        raise ValueError(msg)
    vcf_module, vcf_name = vcf.rsplit(".", 1)
    return StaticFile(
        path="auth/router.py",
        template="init/auth_router.py.j2",
        context={
            "vcf_module": vcf_module,
            "vcf_name": vcf_name,
            "secret_env": getattr(auth, "secret_env", "JWT_SECRET"),
            "algorithm": getattr(auth, "algorithm", "HS256"),
            "token_url": getattr(auth, "token_url", "/auth/token"),
        },
    )
