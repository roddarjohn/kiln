"""Project-scope op: ``api/auth.ts`` + ``auth/Login.tsx``.

Conditional on :attr:`fe.config.ProjectConfig.auth`.

* ``src/api/auth.ts`` -- bridge between glaze's
  :class:`AuthProvider` callbacks and the openapi-ts SDK
  functions for login / validate / logout.
* ``src/auth/Login.tsx`` -- glaze ``<TextField>`` + ``<Button>``
  login form, posting through ``useAuth().login()``.

When no auth section is configured this op yields nothing --
the project is treated as having no login surface, and the
``Scaffold`` op skips importing these files into ``App.tsx``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from fe.config import ProjectConfig
    from foundry.engine import BuildContext


@operation("auth", scope="project")
class Auth:
    """Emit api/auth.ts + auth/Login.tsx when auth is configured."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield auth files iff the project config sets ``auth``."""
        config = ctx.instance
        auth = config.auth

        if auth is None:
            return

        yield StaticFile(
            path="src/api/auth.ts",
            template="src/api/auth.ts.j2",
            context={"auth": auth},
        )

        yield StaticFile(
            path="src/auth/Login.tsx",
            template="src/auth/Login.tsx.j2",
            context={"auth": auth},
        )
