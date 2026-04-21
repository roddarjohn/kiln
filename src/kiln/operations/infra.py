"""Utils operation: shared utility module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.engine import BuildContext


@operation("utils", scope="project")
class Utils:
    """Generate ``utils.py`` with shared route helpers."""

    def build(
        self,
        _ctx: BuildContext,
        _options: BaseModel,
    ) -> list[StaticFile]:
        """Produce the utils static file.

        Args:
            _ctx: Build context (unused).
            _options: Unused (no options).

        Returns:
            Single :class:`StaticFile` for ``utils.py``.

        """
        return [
            StaticFile(
                path="utils.py",
                template="fastapi/utils.py.j2",
            )
        ]
