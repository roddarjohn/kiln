"""Telemetry scaffold operation.

Generates ``telemetry.py`` in the project's output tree when
:attr:`~kiln.config.schema.ProjectConfig.telemetry` is set.  The
file exposes a single ``init_telemetry(app)`` entry point that
builds and installs the configured providers and wires the
requested instrumentors.

Per-handler tracing decorators live in :mod:`ingot.telemetry` and
are imported directly by the generated route modules -- no local
re-export module sits between them.  Likewise OTel runtime
packages aren't emitted as a generated ``requirements.txt``;
generated apps already depend on ``kiln-generator`` (they import
from ``ingot``), so installing ``kiln-generator[opentelemetry]``
is enough.

The op follows the same shape as
:class:`~kiln.operations.scaffold.AuthScaffold` -- gated by a
:meth:`when` predicate so a project without telemetry produces
zero references to OpenTelemetry anywhere in the generated tree.
"""

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from kiln.config.schema import ProjectConfig


@operation("telemetry_scaffold", scope="project")
class TelemetryScaffold:
    """Generate ``telemetry.py``.

    A single :class:`~foundry.outputs.StaticFile` carrying the
    ``init_telemetry(app)`` entry point.  ``init_telemetry`` builds
    the configured tracer / meter / logger providers, installs them
    on the OTel globals, and wires the requested instrumentors.
    The generated function is decorated with
    :func:`ingot.utils.run_once` so a second call is a silent
    no-op rather than a duplicate provider install.
    """

    def when(self, ctx: BuildContext[ProjectConfig, ProjectConfig]) -> bool:
        """Run only when ``ctx.instance.telemetry`` is set.

        Args:
            ctx: Build context with project config.

        Returns:
            ``True`` when the project opts into telemetry.

        """
        return bool(ctx.instance.telemetry)

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the telemetry package files.

        Args:
            ctx: Build context.  ``when`` has already confirmed
                ``ctx.instance.telemetry`` is not ``None``.
            _options: Unused.

        Yields:
            One :class:`~foundry.outputs.StaticFile` per file in the
            ``telemetry/`` package.

        """
        config = ctx.instance
        telemetry = config.telemetry
        assert telemetry is not None  # noqa: S101 -- guaranteed by when()

        package_prefix = config.package_prefix
        telemetry_module = (
            f"{package_prefix}.telemetry" if package_prefix else "telemetry"
        )

        yield StaticFile(
            path="telemetry.py",
            template="init/telemetry_setup.py.j2",
            context={
                "telemetry_module": telemetry_module,
                "service_name": telemetry.service_name,
                "service_version": telemetry.service_version,
                "environment_env": telemetry.environment_env,
                "resource_attributes": dict(telemetry.resource_attributes),
                "traces": telemetry.traces,
                "metrics": telemetry.metrics,
                "logs": telemetry.logs,
                "instrument_fastapi": telemetry.instrument_fastapi,
                "instrument_httpx": telemetry.instrument_httpx,
                "instrument_requests": telemetry.instrument_requests,
                "instrument_logging": telemetry.instrument_logging,
                "sampler": telemetry.sampler,
                "sampler_ratio": telemetry.sampler_ratio,
                "exporter": telemetry.exporter,
            },
        )
