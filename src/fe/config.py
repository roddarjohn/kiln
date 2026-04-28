"""Pydantic schema for fe configs.

The fe target is a thin wrapper over
`@hey-api/openapi-ts <https://heyapi.dev/>`_: it reads its own
jsonnet config and emits an ``openapi-ts.config.ts`` file that
the openapi-ts CLI consumes.  All actual TypeScript codegen
(types + the ``@hey-api/client-fetch`` runtime + the
``@tanstack/react-query`` plugin) happens inside openapi-ts.
"""

from typing import Literal

from pydantic import Field

from foundry.config import FoundryConfig


class ProjectConfig(FoundryConfig):
    """Top-level config for the ``fe`` target.

    Attributes:
        openapi_spec: Path or URL of the OpenAPI 3.x spec
            ``openapi-ts`` should ingest.  Resolved relative to
            the directory ``yarn openapi-ts`` runs from
            (typically the project root).  In the standard
            monorepo layout this is ``"../be/openapi.json"`` --
            the file the backend writes via ``just openapi``.
        output_dir: Where ``openapi-ts`` should write its
            generated client.  Relative to the same project
            root.  Defaults to ``"src/_generated"`` so generated
            code is part of the TypeScript build automatically.
        client: Which ``@hey-api/openapi-ts`` runtime client to
            wire up.  ``"@hey-api/client-fetch"`` is the
            zero-runtime-deps default (uses the platform's
            ``fetch``); ``"@hey-api/client-axios"`` is right
            for projects that already depend on axios.
        react_query: When ``True`` (default), enable the
            ``@tanstack/react-query`` plugin so openapi-ts
            emits ``useQuery`` / ``useMutation`` hooks alongside
            the bare SDK functions.  Off if you only want the
            typed SDK.
        format: Which formatter ``openapi-ts`` should run on
            its output.  ``"prettier"`` works when the consumer
            project ships prettier; ``None`` skips formatting.

    """

    openapi_spec: str = Field(default="../be/openapi.json")
    output_dir: str = Field(default="src/_generated")
    client: Literal[
        "@hey-api/client-fetch",
        "@hey-api/client-axios",
    ] = Field(default="@hey-api/client-fetch")
    react_query: bool = Field(default=True)
    format: Literal["prettier", "biome"] | None = Field(default=None)
