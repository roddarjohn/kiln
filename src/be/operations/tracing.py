"""Tracing operation -- prepends ``@traced_handler`` to CRUD/action routes.

Mirrors :class:`~be.operations.auth.Auth`: resource-scoped,
``after_children=True``, emits nothing.  Walks every
:class:`~be.operations.types.RouteHandler` produced under the
resource and prepends a ``@traced_handler(...)`` decorator string
when telemetry is enabled and not opted out at the resource or
operation level.

Lives as a first-class operation rather than a renderer helper so
the cross-cutting concern is discoverable in the entry-point
registry alongside auth and the modifier ops, and so the
discriminator between CRUD and action handlers is the canonical
:attr:`OperationConfig.type` field rather than a string match on
the body-template path.
"""

from typing import TYPE_CHECKING

from be.operations.types import RouteHandler
from foundry.cascade import cascade
from foundry.naming import Name
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("tracing", scope="resource", after_children=True)
class Tracing:
    """Augment CRUD/action handlers with the tracing decorator.

    Cascades the per-op trace decision through three levels via
    :func:`~be.operations._overrides.cascade`:

    * Operation: :attr:`OperationConfig.trace` (``False`` kills,
      ``True`` forces on, ``None`` inherits).
    * Resource: :attr:`ResourceConfig.trace` (same shape).
    * Project: ``telemetry.span_per_handler`` for CRUD,
      ``telemetry.span_per_action`` for actions.

    The first non-``None`` level wins; ``False`` at any level
    short-circuits to "no span emitted".

    No-op when ``ctx.config.telemetry`` is ``None`` -- generated
    apps without telemetry produce zero references to OpenTelemetry.
    """

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run whenever telemetry is configured at the project level.

        Per-resource and per-op gating lives in :meth:`build`; gating
        here too would duplicate it.
        """
        return bool(ctx.config.telemetry)

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Prepend ``@traced_handler`` to handlers whose op opts in."""
        telemetry = ctx.config.telemetry
        assert telemetry is not None  # noqa: S101 -- guaranteed by when()
        resource = ctx.instance

        # Project-level fallback varies by op type, so resolve per
        # op via the cascade primitive directly rather than the
        # bulk ``resolve_op_overrides`` helper.
        traced_ops = {
            op.name
            for op in resource.operations
            if cascade(
                op.trace,
                resource.trace,
                telemetry.span_per_action
                if op.type == "action"
                else telemetry.span_per_handler,
                disable=False,
            )
        }
        # Lowercase model class -- dashboards read ``article.get``
        # better than ``Article.get``.
        _, model = Name.from_dotted(resource.model)
        label = model.lower

        for handler in ctx.store.outputs_under(ctx.instance_id, RouteHandler):
            if handler.op_name not in traced_ops:
                continue

            handler.decorators.insert(
                0,
                f'@traced_handler("{label}.{handler.op_name}", '
                f'resource="{label}", op="{handler.op_name}")',
            )
            handler.extra_imports.append(("ingot.telemetry", "traced_handler"))

        return ()
