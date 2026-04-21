"""Auth operation: augments CRUD handlers with auth dependencies.

Auth is a resource-scoped operation that runs *after* the CRUD
and action operations have produced their route handlers and
test cases.  When the project has auth configured and the
current resource opts in, it:

* Appends ``current_user: Annotated[dict, Depends(...)]`` to
  each :class:`RouteHandler`'s ``extra_deps`` so the template
  renders the auth dependency.
* Appends the ``get_current_user`` import to the handler's
  ``extra_imports`` so the assembler includes it.
* Flips :attr:`TestCase.requires_auth` so the generated tests
  expect a 401 without credentials.

This is the first example of an operation in the augment role:
it produces no new outputs, only mutates earlier ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry.naming import prefix_import
from foundry.operation import operation
from foundry.outputs import RouteHandler, TestCase

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.engine import BuildContext


@operation(
    "auth",
    scope="resource",
    requires=["get", "list", "create", "update", "delete", "action"],
)
class Auth:
    """Augment CRUD/action handlers and tests with auth."""

    def when(self, ctx: BuildContext) -> bool:
        """Apply only when auth is configured and the resource opts in.

        Args:
            ctx: Build context for the current resource.

        Returns:
            ``True`` when the project config has ``auth`` set
            and the resource has ``require_auth`` (default
            ``True``).

        """
        if getattr(ctx.config, "auth", None) is None:
            return False
        return bool(getattr(ctx.instance, "require_auth", True))

    def build(
        self,
        ctx: BuildContext,
        _options: BaseModel,
    ) -> list[object]:
        """Mutate earlier handlers/tests to require auth.

        Args:
            ctx: Build context with store of earlier outputs.
            _options: Unused.

        Returns:
            Empty list -- this operation only mutates.

        """
        auth_mod = prefix_import(
            ctx.package_prefix,
            "auth",
            "dependencies",
        )
        dep_line = "current_user: Annotated[dict, Depends(get_current_user)],"
        import_pair = (auth_mod, "get_current_user")

        items = ctx.store.get_by_scope(ctx.scope.name, ctx.instance_id)
        for obj in items:
            if isinstance(obj, RouteHandler):
                obj.extra_deps.append(dep_line)
                obj.extra_imports.append(import_pair)
            elif isinstance(obj, TestCase):
                obj.requires_auth = True

        return []
