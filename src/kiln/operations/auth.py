"""Auth operation: augments CRUD handlers with a session dependency.

Auth is a resource-scoped operation that runs *after* the CRUD
and action operations have produced their route handlers and
test cases.  When the project has auth configured and the
current resource opts in, it:

* Appends ``session: Annotated[dict, Depends(...)]`` to each
  :class:`RouteHandler`'s ``extra_deps`` so the template renders
  the auth dependency.
* Appends the ``get_session`` import to the handler's
  ``extra_imports`` so the assembler includes it.  The imported
  name is aliased from the consumer's
  :attr:`~kiln.config.schema.AuthConfig.get_session_fn`.
* Flips :attr:`TestCase.requires_auth` so the generated tests
  expect a 401 without credentials.

This is the first example of an operation in the augment role:
it produces no new outputs, only mutates earlier ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from foundry.naming import prefix_import
from foundry.operation import operation
from kiln.operations.types import RouteHandler, TestCase

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from kiln.config.schema import AuthConfig, ResourceConfig


@operation("auth", scope="resource", after_children=True)
class Auth:
    """Augment CRUD/action handlers and tests with auth.

    Runs at resource scope with ``after_children=True`` so all
    operation-scope ops under this resource have already produced
    their handlers and test cases by the time auth sweeps through.
    """

    def when(self, ctx: BuildContext[ResourceConfig]) -> bool:
        """Run whenever auth is configured.

        Per-op filtering happens in :meth:`build`; gating here on
        "any op opts in" would duplicate that logic.  The cost of
        an unconditional pass is one no-op loop over empty handler
        lists when nothing ends up needing auth.
        """
        return getattr(ctx.config, "auth", None) is not None

    def build(
        self,
        ctx: BuildContext[ResourceConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Mutate handlers/tests per effective per-op ``require_auth``.

        Each op's effective auth is its own ``require_auth`` when set,
        else the resource-level default.  Handlers from ops that
        don't require auth are left alone so no spurious session
        dependency leaks into open routes.

        Args:
            ctx: Build context with store of earlier outputs.
            _options: Unused.

        Returns:
            Empty iterable -- this operation only mutates earlier
            output and emits no new objects.

        """
        auth_cfg = cast("AuthConfig", getattr(ctx.config, "auth", None))
        session_module, session_name = auth_cfg.session_schema.rsplit(".", 1)
        deps_module = prefix_import(ctx.package_prefix, "auth", "dependencies")

        resource_default = ctx.instance.require_auth
        op_auth: dict[str, bool] = {
            op.name: (
                op.require_auth
                if op.require_auth is not None
                else resource_default
            )
            for op in ctx.instance.operations
        }

        for handler in ctx.store.outputs_under(ctx.instance_id, RouteHandler):
            if not op_auth.get(handler.op_name, False):
                continue
            handler.extra_deps.append(
                f"session: Annotated[{session_name}, Depends(get_session)],"
            )
            handler.extra_imports.append((deps_module, "get_session"))
            handler.extra_imports.append((session_module, session_name))

        for test in ctx.store.outputs_under(ctx.instance_id, TestCase):
            # TestCase.op_name holds the op class for actions
            # ("action"); the concrete instance name is in
            # action_name.  For CRUD the two match.
            instance_name = test.action_name or test.op_name
            test.requires_auth = op_auth.get(instance_name, False)

        return ()
