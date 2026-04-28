"""Project-scope operation that emits ``openapi-ts.config.ts``.

A single :class:`OpenApiTsConfig` op runs at the project scope
and yields one :class:`~foundry.outputs.StaticFile` -- the
``openapi-ts.config.ts`` file at the project root.  The actual
TypeScript codegen is delegated to
`@hey-api/openapi-ts <https://heyapi.dev/>`_; this op just
keeps the openapi-ts config in lockstep with the kiln-side
jsonnet config.

``just generate`` chains the two phases::

    foundry generate --target fe --config config/fe.jsonnet --out .
    yarn openapi-ts

so the user's mental model stays "edit jsonnet, run just
generate, get fresh TS".
"""

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from fe.config import ProjectConfig
    from foundry.engine import BuildContext


@operation("openapi_ts_config", scope="project")
class OpenApiTsConfig:
    """Emit ``openapi-ts.config.ts`` from the fe project config."""

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield a single ``openapi-ts.config.ts`` :class:`~foundry.outputs.StaticFile`.

        Args:
            ctx: Build context carrying the validated
                :class:`~fe.config.ProjectConfig`.
            _options: Unused (no per-op options).

        Yields:
            A :class:`~foundry.outputs.StaticFile` for
            ``openapi-ts.config.ts`` at the project root.

        """
        config = ctx.instance

        plugins: list[str] = [config.client]

        if config.react_query:
            plugins.append("@tanstack/react-query")

        plugins.append("@hey-api/typescript")
        plugins.append("@hey-api/sdk")

        yield StaticFile(
            path="openapi-ts.config.ts",
            template="openapi-ts.config.ts.j2",
            context={
                "openapi_spec": config.openapi_spec,
                "output_dir": config.output_dir,
                "format": config.format,
                "plugins": plugins,
            },
        )
