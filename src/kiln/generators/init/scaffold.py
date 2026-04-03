"""Scaffold generator for infrastructure files.

Produces ``db/`` and ``auth/`` boilerplate driven entirely by the
project config.  All files are overwritten on every run so that
changes to auth strategy or database pool settings are always
reflected without manual editing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import AuthConfig, KilnConfig


class ScaffoldGenerator:
    """Generates infrastructure scaffold files from a kiln config.

    Call :meth:`generate` to produce ``db/`` and ``auth/`` files.
    """

    def generate(self, config: KilnConfig) -> list[GeneratedFile]:
        """Return all scaffold files for *config*.

        Generates per-database session files when ``config.databases``
        is non-empty, or a single ``db/session.py`` otherwise.  Emits
        ``auth/dependencies.py`` only when ``config.auth`` is set.

        Args:
            config: The validated project or app-level kiln config.

        Returns:
            List of :class:`~kiln.generators.base.GeneratedFile`
            objects.

        """
        files: list[GeneratedFile] = [
            GeneratedFile("db/__init__.py", ""),
        ]
        files.extend(_render_sessions(config))
        if config.auth is not None:
            files += [
                GeneratedFile("auth/__init__.py", ""),
                GeneratedFile(
                    "auth/dependencies.py", _render_auth_deps(config.auth)
                ),
            ]
        return files


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_sessions(config: KilnConfig) -> list[GeneratedFile]:
    """Return one session file per database, or the legacy single file."""
    session_tmpl = env.get_template("init/db_session.py.j2")
    if config.databases:
        return [
            GeneratedFile(
                f"db/{db.key}_session.py",
                session_tmpl.render(
                    key=db.key,
                    url_env=db.url_env,
                    echo=db.echo,
                    pool_size=db.pool_size,
                    max_overflow=db.max_overflow,
                    pool_timeout=db.pool_timeout,
                    pool_recycle=db.pool_recycle,
                    pool_pre_ping=db.pool_pre_ping,
                    get_db_fn=f"get_{db.key}_db",
                ),
            )
            for db in config.databases
        ]
    return [
        GeneratedFile(
            "db/session.py",
            session_tmpl.render(
                key=None,
                url_env="DATABASE_URL",
                echo=False,
                pool_size=5,
                max_overflow=10,
                pool_timeout=30,
                pool_recycle=-1,
                pool_pre_ping=True,
                get_db_fn="get_db",
            ),
        ),
    ]


def _render_auth_deps(auth: AuthConfig) -> str:
    """Render ``auth/dependencies.py`` from *auth* config."""
    gcu_module: str | None = None
    gcu_name: str | None = None
    if auth.get_current_user_fn:
        gcu_module, gcu_name = auth.get_current_user_fn.rsplit(".", 1)
    return env.get_template("init/auth_dependencies.py.j2").render(
        gcu_module=gcu_module,
        gcu_name=gcu_name,
        secret_env=auth.secret_env,
        algorithm=auth.algorithm,
        token_url=auth.token_url,
    )
