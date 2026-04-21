"""Utils operation: shared utility module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext


@operation("utils", scope="project")
class Utils:
    """Generate ``utils.py`` with shared route helpers."""

    def build(
        self,
        _ctx: BuildContext,
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the utils static file.

        Args:
            _ctx: Build context (unused).
            _options: Unused (no options).

        Yields:
            Single :class:`StaticFile` for ``utils.py``.

        """
        yield StaticFile(
            path="utils.py",
            template="fastapi/utils.py.j2",
        )
