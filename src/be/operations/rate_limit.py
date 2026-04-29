"""Rate-limit operation -- prepends ``@limiter.limit`` to handlers.

Mirrors ``be.operations.tracing.Tracing``: resource-scoped,
``after_children=True``, emits nothing.  Walks every
:class:`~be.operations.types.RouteHandler` produced under the
resource and prepends a ``@limiter.limit("...")`` decorator string
when an effective rate limit resolves at the project / resource /
op chain.

slowapi's decorator inspects the handler signature for a
``request: Request`` parameter and binds the limiter check to it
at request time.  We inject one when the handler doesn't already
declare it, since the rest of the be CRUD/action stack doesn't
need the request directly.
"""

from typing import TYPE_CHECKING

from be.operations.types import RouteHandler, RouteParam
from foundry.cascade import cascade
from foundry.naming import prefix_import
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("rate_limit", scope="resource", after_children=True)
class RateLimit:
    """Augment CRUD/action handlers with the slowapi rate-limit decorator.

    Effective limit per op:

    * Op's ``rate_limit`` when set (``False`` short-circuits, a
      string overrides).
    * Else the resource's ``rate_limit`` (same semantics).
    * Else :attr:`~be.config.schema.RateLimitConfig.default_limit`,
      when configured.
    * Else no decorator -- the handler is unlimited.

    No-op when ``ctx.config.rate_limit`` is ``None`` -- generated
    apps without rate limiting produce zero references to slowapi.
    """

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run whenever rate limiting is configured at the project level.

        Per-resource and per-op gating lives in :meth:`build`; gating
        here too would duplicate it.
        """
        return bool(ctx.config.rate_limit)

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Prepend ``@limiter.limit`` to handlers whose op opts in."""
        rate_limit_cfg = ctx.config.rate_limit
        assert rate_limit_cfg is not None  # noqa: S101 -- guaranteed by when()
        resource = ctx.instance

        # Cascade: op.rate_limit → resource.rate_limit →
        # project.rate_limit.default_limit.  ``False`` at any level
        # short-circuits to ``None`` (no decorator emitted).
        op_limits = {
            op.name: cascade(
                op.rate_limit,
                resource.rate_limit,
                rate_limit_cfg.default_limit,
                disable=False,
            )
            for op in resource.operations
        }

        rate_limit_module = prefix_import(ctx.package_prefix, "rate_limit")

        for handler in ctx.store.outputs_under(ctx.instance_id, RouteHandler):
            limit = op_limits.get(handler.op_name)

            if limit is None:
                continue

            handler.decorators.insert(0, f'@limiter.limit("{limit}")')
            handler.extra_imports.append((rate_limit_module, "limiter"))
            # slowapi reads the limit metadata off the request at
            # runtime, so the parameter has to be on the signature
            # even when the handler body never references it.
            # ``add_param`` is a no-op when one is already present;
            # ``ImportCollector`` dedupes the import at emit time.
            handler.add_param(
                RouteParam(name="request", annotation="Request"),
                prepend=True,
            )
            handler.extra_imports.append(("fastapi", "Request"))

        return ()
