"""Auth operation -- augments handlers + tests after the CRUD sweep.

Resource-scoped, ``after_children=True``, emits nothing.  Stamps a
``Depends(get_session)`` parameter onto each handler whose op has
effective ``require_auth`` and flips
:attr:`~be.operations.types.TestCase.requires_auth` to match.
"""

from typing import TYPE_CHECKING

from be.operations.types import RouteHandler, TestCase
from foundry.naming import prefix_import
from foundry.operation import operation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, ResourceConfig
    from foundry.engine import BuildContext


@operation("auth", scope="resource", after_children=True)
class Auth:
    """Augment CRUD/action handlers and tests with auth."""

    def when(self, ctx: BuildContext[ResourceConfig, ProjectConfig]) -> bool:
        """Run whenever auth is configured.

        Per-op filtering lives in :meth:`build`; gating here too
        would duplicate it.
        """
        return bool(ctx.config.auth)

    def build(
        self,
        ctx: BuildContext[ResourceConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[object]:
        """Stamp session dep onto handlers whose op opts in.

        Effective auth = op's ``require_auth`` when set, else the
        resource default.  Skipping non-auth ops keeps the session
        dep from leaking onto open routes.
        """
        auth_cfg = ctx.config.auth
        assert auth_cfg is not None  # noqa: S101 -- guaranteed by when()
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

        # The action framework needs a session to evaluate guards.
        # If include_actions_in_dump or permissions_endpoint is
        # set, force-include the dep on every handler under this
        # resource regardless of the per-op require_auth setting --
        # otherwise the generated handler / serializer code would
        # reference an undeclared ``session`` parameter.
        # ``searchable`` is included because the resource-level
        # ``_values`` endpoint passes ``session`` through to the
        # link builder, and any consumer using a ``serializer:``
        # hook also expects session in scope.
        force_session = (
            ctx.instance.include_actions_in_dump
            or ctx.instance.permissions_endpoint
            or ctx.instance.searchable
        )

        for handler in ctx.store.outputs_under(ctx.instance_id, RouteHandler):
            if not (force_session or op_auth.get(handler.op_name, False)):
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
