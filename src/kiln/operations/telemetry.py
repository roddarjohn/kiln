"""Telemetry scaffold operation.

Generates the ``telemetry/`` package in the project's output tree
when :attr:`~kiln.config.schema.ProjectConfig.telemetry` is set.
The package contains the OpenTelemetry initialisation entry point
(``setup.py``) and the per-handler tracing decorators
(``decorators.py``).

The op follows the same shape as
:class:`~kiln.operations.scaffold.AuthScaffold` -- gated by a
:meth:`when` predicate so a project without telemetry produces
zero references to OpenTelemetry anywhere in the generated tree.

OTel runtime packages aren't emitted as a generated
``requirements.txt`` -- generated apps already depend on
``kiln-generator`` (they import from ``ingot``), so the consumer
simply installs ``kiln-generator[opentelemetry]`` and gets the
pinned package set from there.
"""

from __future__ import annotations

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
    """Generate the ``telemetry/`` package.

    Emits three :class:`~foundry.outputs.StaticFile` outputs:

    * ``telemetry/__init__.py`` -- package marker.
    * ``telemetry/setup.py`` -- ``init_telemetry(app)`` builds and
      installs the configured tracer / meter / logger providers and
      wires the requested instrumentors.
    * ``telemetry/decorators.py`` -- re-exports the per-handler
      tracing decorators from :mod:`ingot.telemetry` so generated
      route modules import them via a stable local path.
    """

    def when(self, ctx: BuildContext[ProjectConfig]) -> bool:
        """Run only when ``ctx.instance.telemetry`` is set.

        Args:
            ctx: Build context with project config.

        Returns:
            ``True`` when the project opts into telemetry.

        """
        return ctx.instance.telemetry is not None

    def build(
        self,
        ctx: BuildContext[ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the telemetry package files.

        Args:
            ctx: Build context.  ``when`` has already confirmed
                ``ctx.instance.telemetry`` is not ``None``.
            _options: Unused.

        Yields:
            One :class:`StaticFile` per file in the
            ``telemetry/`` package.

        """
        config = ctx.instance
        telemetry = config.telemetry
        assert telemetry is not None  # noqa: S101 -- guaranteed by when()

        package_prefix = config.package_prefix
        telemetry_module = (
            f"{package_prefix}.telemetry" if package_prefix else "telemetry"
        )

        setup_context = {
            "telemetry_module": telemetry_module,
            "service_name": telemetry.service_name,
            "service_version": telemetry.service_version,
            "environment": telemetry.environment,
            "resource_attributes": dict(telemetry.resource_attributes),
            "traces": telemetry.traces,
            "metrics": telemetry.metrics,
            "logs": telemetry.logs,
            "instrument_fastapi": telemetry.instrument_fastapi,
            "instrument_httpx": telemetry.instrument_httpx,
            "instrument_logging": telemetry.instrument_logging,
            "sampler": telemetry.sampler,
            "sampler_ratio": telemetry.sampler_ratio,
            "exporter": telemetry.exporter,
            "exporter_endpoint_env": telemetry.exporter_endpoint_env,
            "exporter_headers_env": telemetry.exporter_headers_env,
        }

        yield StaticFile(
            path="telemetry/__init__.py",
            template="",
            context={},
        )
        yield StaticFile(
            path="telemetry/setup.py",
            template="init/telemetry_setup.py.j2",
            context=setup_context,
        )
        yield StaticFile(
            path="telemetry/decorators.py",
            template="init/telemetry_decorators.py.j2",
            context={"telemetry_module": telemetry_module},
        )
