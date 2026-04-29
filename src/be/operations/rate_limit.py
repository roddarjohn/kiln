"""Rate-limit operation -- prepends ``@limiter.limit`` to handlers.

Mirrors :class:`~be.operations.tracing.Tracing`: resource-scoped,
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
    * Else :attr:`RateLimitConfig.default_limit`, when configured.
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

        # Resource-level ``False`` short-circuits every op on this
        # resource regardless of project default or per-op settings.
        if resource.rate_limit is False:
            return ()

        resource_limit: str | None

        if isinstance(resource.rate_limit, str):
            resource_limit = resource.rate_limit

        else:
            # ``None`` falls through to the project default.
            resource_limit = rate_limit_cfg.default_limit

        op_limits: dict[str, str | None] = {}

        for op in resource.operations:
            if op.rate_limit is False:
                op_limits[op.name] = None

            elif isinstance(op.rate_limit, str):
                op_limits[op.name] = op.rate_limit

            else:
                op_limits[op.name] = resource_limit

        rate_limit_module = prefix_import(ctx.package_prefix, "rate_limit")

        for handler in ctx.store.outputs_under(ctx.instance_id, RouteHandler):
            limit = op_limits.get(handler.op_name)

            if limit is None:
                continue

            handler.decorators.insert(0, f'@limiter.limit("{limit}")')
            handler.extra_imports.append((rate_limit_module, "limiter"))

            if not _has_request_param(handler):
                # slowapi reads the limit metadata off the request
                # at runtime, so the parameter has to be on the
                # signature even when the handler body never
                # references it.  Insert at the front so optional /
                # default-bearing params stay last.
                handler.params.insert(
                    0,
                    RouteParam(name="request", annotation="Request"),
                )
                handler.extra_imports.append(("fastapi", "Request"))

        return ()


def _has_request_param(handler: RouteHandler) -> bool:
    """Return whether *handler* already declares ``request: Request``."""
    return any(
        param.name == "request" and param.annotation == "Request"
        for param in handler.params
    )
