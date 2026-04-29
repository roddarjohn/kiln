"""Rate-limit scaffold operation.

Generates ``rate_limit.py`` in the project's output tree when
:attr:`~be.config.schema.ProjectConfig.rate_limit` is set.  The
file exposes the slowapi ``limiter`` plus an
``init_rate_limiter(app)`` entry point that installs limiter state,
the ``RateLimitExceeded`` handler, and the ``SlowAPIMiddleware``.

Per-handler ``@limiter.limit(...)`` decoration lives in
:mod:`be.operations.rate_limit` -- this op only emits the shared
limiter module.  Mirrors ``be.operations.telemetry.TelemetryScaffold``.
"""

from typing import TYPE_CHECKING

from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig
    from foundry.engine import BuildContext


_DEFAULT_KEY_FUNC = "ingot.rate_limit.default_key_func"
"""Fallback key callable when ``rate_limit.key_func`` is unset."""


@operation("rate_limit_scaffold", scope="project")
class RateLimitScaffold:
    """Generate ``rate_limit.py``.

    A single :class:`~foundry.outputs.StaticFile` carrying the
    ``init_rate_limiter(app)`` entry point and the project-wide
    ``limiter`` object that per-handler decorators import.  The
    function is decorated with :func:`ingot.utils.run_once` in the
    template so a second call is a silent no-op rather than a
    duplicate middleware install.
    """

    def when(self, ctx: BuildContext[ProjectConfig, ProjectConfig]) -> bool:
        """Run only when ``ctx.instance.rate_limit`` is set.

        Args:
            ctx: Build context with project config.

        Returns:
            ``True`` when the project opts into rate limiting.

        """
        return bool(ctx.instance.rate_limit)

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the rate-limit setup module.

        Args:
            ctx: Build context.  ``when`` has already confirmed
                ``ctx.instance.rate_limit`` is not ``None``.
            _options: Unused.

        Yields:
            One :class:`~foundry.outputs.StaticFile` for ``rate_limit.py``.

        """
        config = ctx.instance
        rate_limit = config.rate_limit
        assert rate_limit is not None  # noqa: S101 -- guaranteed by when()

        package_prefix = config.package_prefix
        rate_limit_module = (
            f"{package_prefix}.rate_limit" if package_prefix else "rate_limit"
        )

        bucket_module, bucket_name_obj = Name.from_dotted(
            rate_limit.bucket_model
        )
        bucket_class = bucket_name_obj.raw

        key_func_dotted = rate_limit.key_func or _DEFAULT_KEY_FUNC
        key_func_module, key_func_name_obj = Name.from_dotted(key_func_dotted)
        key_func_name = key_func_name_obj.raw

        database = config.resolve_database(rate_limit.db_key)

        yield StaticFile(
            path="rate_limit.py",
            template="init/rate_limit_setup.py.j2",
            context={
                "rate_limit_module": rate_limit_module,
                "bucket_module": bucket_module,
                "bucket_class": bucket_class,
                "key_func_module": key_func_module,
                "key_func_name": key_func_name,
                "default_limit": rate_limit.default_limit,
                "headers_enabled": rate_limit.headers_enabled,
                "url_env": database.url_env,
                "db_key": database.key,
            },
        )
