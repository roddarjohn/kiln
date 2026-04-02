"""Scaffold generator for ``kiln init``.

Produces the one-time boilerplate files that do not change when
the config is updated: the SQLAlchemy/pgcraft base, the async
session factory, and the JWT authentication dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiln.generators._env import env
from kiln.generators.base import GeneratedFile

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig


class ScaffoldGenerator:
    """Generates one-time scaffold files for a new kiln project.

    Call :meth:`generate` to produce the boilerplate files.
    These files are written with ``overwrite=False`` so that
    ``kiln init`` never overwrites hand-edited scaffolds.
    """

    def generate(self, config: KilnConfig | None = None) -> list[GeneratedFile]:
        """Return all scaffold files.

        When *config* includes a ``databases`` list, one session file is
        generated per database (e.g. ``db/primary_session.py``).  Without
        a databases config the legacy single-file layout is used
        (``db/session.py``).

        Args:
            config: Optional project config.  When supplied, database
                session files are generated per database entry.

        Returns:
            List of :class:`~kiln.generators.base.GeneratedFile`
            objects, all with ``overwrite=False``.

        """
        session_tmpl = env.get_template("init/db_session.py.j2")

        databases = config.databases if config else []
        if databases:
            session_files = [
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
                    overwrite=False,
                )
                for db in databases
            ]
        else:
            session_files = [
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
                    overwrite=False,
                )
            ]

        return [
            GeneratedFile("auth/__init__.py", "", overwrite=False),
            GeneratedFile(
                "auth/dependencies.py",
                env.get_template("init/auth_dependencies.py.j2").render(),
                overwrite=False,
            ),
            GeneratedFile("db/__init__.py", "", overwrite=False),
            GeneratedFile(
                "db/base.py",
                env.get_template("init/db_base.py.j2").render(),
                overwrite=False,
            ),
            *session_files,
        ]
