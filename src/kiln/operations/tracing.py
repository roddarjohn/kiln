"""Tracing operation -- prepends ``@traced_handler`` to CRUD/action routes.

Mirrors :class:`~kiln.operations.auth.Auth`: resource-scoped,
``after_children=True``, emits nothing.  Walks every
:class:`RouteHandler` produced under the resource and prepends a
``@traced_handler(...)`` decorator string when telemetry is enabled
and not opted out at the resource or operation level.

Lives as a first-class operation rather than a renderer helper so
the cross-cutting concern is discoverable in the entry-point
registry alongside auth and the modifier ops, and so the
discriminator between CRUD and action handlers is the canonical
:attr:`OperationConfig.type` field rather than a string match on
the body-template path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.operation import operation
from kiln.operations.types import RouteHandler

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from kiln.config.schema import ProjectConfig, ResourceConfig


@operation("tracing", scope="resource", after_children=True)
class Tracing:
    """Augment CRUD/action handlers with the tracing decorator.

    Composes the project / resource / op gates:

    * Project: ``telemetry.span_per_handler`` for CRUD,
      ``telemetry.span_per_action`` for actions.
    * Resource: :attr:`ResourceConfig.trace` (``None`` inherits,
      ``False`` disables for every op on this resource).
    * Operation: :attr:`OperationConfig.trace` (same inheritance).

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
        if resource.trace is False:
            return ()

        traced_ops = {
            op.name
            for op in resource.operations
            if op.trace is not False
            and (
                telemetry.span_per_action
                if op.type == "action"
                else telemetry.span_per_handler
            )
        }
        # Lowercase model class -- dashboards read ``article.get``
        # better than ``Article.get``.
        label = resource.model.rpartition(".")[2].lower()
        record = telemetry.record_exceptions

        for handler in ctx.store.outputs_under(ctx.instance_id, RouteHandler):
            if handler.op_name not in traced_ops:
                continue
            handler.decorators.insert(
                0,
                f'@traced_handler("{label}.{handler.op_name}", '
                f'resource="{label}", op="{handler.op_name}", '
                f"record_exceptions={record})",
            )
            handler.extra_imports.append(("ingot.telemetry", "traced_handler"))

        return ()
