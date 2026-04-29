"""App-scope op that emits the per-app action registry module.

Walks each :class:`~be.config.schema.ResourceConfig` in an app and
builds two tuples of :class:`ingot.actions.ActionSpec` per
resource -- one for object-scope ops (``get``, ``update``,
``delete``, custom object actions), one for collection-scope ops
(``list``, ``create``, custom collection actions).  Generated
serializers and route handlers import these tuples to populate
``actions`` fields on responses, drive the ``/permissions``
endpoint, and gate handler execution on the same guard.

The module is only emitted when at least one resource in the app
opts in to the action framework.  A resource opts in by setting
:attr:`~be.config.schema.ResourceConfig.include_actions_in_dump`,
:attr:`~be.config.schema.ResourceConfig.permissions_endpoint`, or
by configuring ``can`` on at least one of its operations.
Otherwise the file is skipped so projects that don't use the
framework see no extra modules in their generated tree.
"""

from typing import TYPE_CHECKING, cast

from be.operations._introspect import introspect_action_fn
from be.operations._naming import collection_specs_const, object_specs_const
from foundry.naming import Name
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import (
        App,
        OperationConfig,
        ProjectConfig,
        ResourceConfig,
    )
    from foundry.engine import BuildContext


_BUILTIN_OBJECT_OPS = frozenset({"get", "update", "delete"})
"""Built-in CRUD ops that target a single resource instance."""

_BUILTIN_COLLECTION_OPS = frozenset({"list", "create"})
"""Built-in CRUD ops that target the resource collection."""


@operation("actions", scope="app", after_children=True)
class Actions:
    """Generate ``{app_module}/actions.py`` with per-resource registries.

    Runs in the post-children phase of the app scope so every
    resource's :class:`~be.config.schema.OperationConfig` list is
    fully visited before the registry is rendered.  Built-in CRUD
    ops are classified by name; ``type == "action"`` ops are
    introspected via ``introspect_action_fn`` (in
    ``be.operations._introspect``) to determine object vs.
    collection scope.
    """

    def build(
        self,
        ctx: BuildContext[App, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the app's action-registry module, when needed.

        Args:
            ctx: Build context for one
                :class:`~be.config.schema.App`.
            _options: Unused.

        Yields:
            A single :class:`~foundry.outputs.StaticFile` for
            ``{module}/actions.py`` when at least one resource in
            this app participates in the action framework;
            nothing otherwise.

        """
        app = ctx.instance
        module = app.config.module

        resources_ctx: list[dict[str, object]] = []
        guard_imports: dict[str, set[str]] = {}

        for _, resource_obj in ctx.store.children(
            ctx.instance_id, child_scope="resource"
        ):
            resource = cast("ResourceConfig", resource_obj)

            if not _resource_participates(resource):
                continue

            entry = _build_resource_entry(resource, guard_imports)

            if entry is not None:
                resources_ctx.append(entry)

        if not resources_ctx:
            return

        # Sort imports for deterministic output.  Each entry is a
        # ``(module, [name, ...])`` tuple with names alphabetized.
        sorted_imports = [
            (mod, sorted(names)) for mod, names in sorted(guard_imports.items())
        ]

        yield StaticFile(
            path=f"{module}/actions.py",
            template="fastapi/actions.py.j2",
            context={
                "module": module,
                "guard_imports": sorted_imports,
                "resources": resources_ctx,
            },
        )


def _resource_participates(resource: ResourceConfig) -> bool:
    """Return ``True`` when *resource* needs registry entries.

    A resource participates when it opts in to the dump, the
    permissions endpoint, or has at least one operation with a
    ``can`` callable configured.  Otherwise the registry would
    only contain ``always_true`` guards no consumer references --
    pure churn.
    """
    if resource.include_actions_in_dump or resource.permissions_endpoint:
        return True

    return any(op.can is not None for op in resource.operations)


def _build_resource_entry(
    resource: ResourceConfig,
    guard_imports: dict[str, set[str]],
) -> dict[str, object] | None:
    """Build the template context for one resource's registries.

    Mutates *guard_imports* in place: each non-default ``can``
    dotted path is split and accumulated as
    ``{module: {names}}`` so the template can render a single
    ``from module import a, b, c`` line per source module.

    Returns ``None`` when the resource declares no actionable ops
    (only modifiers, say) -- the resource section is then omitted
    entirely so the rendered file stays clean.
    """
    _, model = Name.from_dotted(resource.model)

    object_actions: list[dict[str, str]] = []
    collection_actions: list[dict[str, str]] = []

    for op in resource.operations:
        classified = _classify(op, resource.model)

        if classified is None:
            continue

        is_object_action, action_name = classified
        can_ref = _resolve_can_ref(op, guard_imports)
        target = object_actions if is_object_action else collection_actions
        target.append({"name": action_name, "can": can_ref})

    if not object_actions and not collection_actions:
        return None

    return {
        "object_const": object_specs_const(model),
        "collection_const": collection_specs_const(model),
        "object_actions": object_actions,
        "collection_actions": collection_actions,
    }


def _classify(
    op: OperationConfig,
    model_class_path: str,
) -> tuple[bool, str] | None:
    """Return ``(is_object_action, name)`` for *op*, or ``None`` to skip.

    Built-in CRUD ops dispatch on :attr:`OperationConfig.name`;
    custom action ops dispatch on ``type == "action"`` and need
    introspection to learn whether the consumer's function takes a
    model instance.  Anything else (modifiers, unknown op types)
    is silently skipped -- the action framework only registers
    independently-invokable operations.
    """
    if op.type == "action":
        fn = op.options.get("fn")

        if not isinstance(fn, str):
            return None

        info = introspect_action_fn(fn, model_class_path)
        return info.is_object_action, op.name

    if op.type is not None:
        return None

    if op.name in _BUILTIN_OBJECT_OPS:
        return True, op.name

    if op.name in _BUILTIN_COLLECTION_OPS:
        return False, op.name

    return None


def _resolve_can_ref(
    op: OperationConfig,
    guard_imports: dict[str, set[str]],
) -> str:
    """Return the Python expression naming this op's guard.

    When ``op.can`` is unset, falls back to ``always_true`` (which
    the template imports unconditionally).  Otherwise splits the
    dotted path, registers the import, and returns the bare
    callable name -- the template emits ``from <module> import
    <name>`` once per source module so multiple resources sharing
    a guard module produce a single import line.
    """
    if op.can is None:
        return "always_true"

    module_path, _, attr = op.can.rpartition(".")

    if not module_path or not attr:
        msg = (
            f"Operation {op.name!r}: can must be a dotted path (got {op.can!r})"
        )
        raise ValueError(msg)

    guard_imports.setdefault(module_path, set()).add(attr)
    return attr
