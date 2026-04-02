"""Scaffold generator for ``kiln init``.

Produces the one-time boilerplate files that do not change when
the config is updated: the SQLAlchemy/pgcraft base, the async
session factory, and the JWT authentication dependency.
"""

from __future__ import annotations

from kiln.generators._env import env
from kiln.generators.base import GeneratedFile


class ScaffoldGenerator:
    """Generates one-time scaffold files for a new kiln project.

    Call :meth:`generate` to produce the boilerplate files.
    These files are written with ``overwrite=False`` so that
    ``kiln init`` never overwrites hand-edited scaffolds.
    """

    def generate(self) -> list[GeneratedFile]:
        """Return all scaffold files.

        Returns:
            List of :class:`~kiln.generators.base.GeneratedFile`
            objects, all with ``overwrite=False``.

        """
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
            GeneratedFile(
                "db/session.py",
                env.get_template("init/db_session.py.j2").render(),
                overwrite=False,
            ),
        ]
