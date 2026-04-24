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
        """Apply only when auth is configured and the resource opts in.

        Args:
            ctx: Build context for the current resource.

        Returns:
            ``True`` when the project config has ``auth`` set
            and the resource has ``require_auth`` (default
            ``True``).

        """
        return (
            getattr(ctx.config, "auth", None) is not None
            and ctx.instance.require_auth
        )

    def build(
        self,
        ctx: BuildContext[ResourceConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Mutate earlier handlers/tests to require auth.

        Args:
            ctx: Build context with store of earlier outputs.
            _options: Unused.

        Returns:
            Empty iterable -- this operation only mutates earlier
            output and emits no new objects.

        """
        auth_cfg = cast("AuthConfig", getattr(ctx.config, "auth", None))
        session_module, session_name = auth_cfg.session_schema.rsplit(".", 1)
        deps_module = prefix_import(
            ctx.package_prefix, "auth", "dependencies"
        )

        for handler in ctx.store.outputs_under(ctx.instance_id, RouteHandler):
            handler.extra_deps.append(
                f"session: Annotated[{session_name}, Depends(get_session)],"
            )
            handler.extra_imports.append((deps_module, "get_session"))
            handler.extra_imports.append((session_module, session_name))

        for test in ctx.store.outputs_under(ctx.instance_id, TestCase):
            test.requires_auth = True

        return ()
