"""New-protocol generation entry point.

Config in, files out.  Uses the :class:`~foundry.engine.Engine`
to run operations, then the assembler to produce files.

Multi-app configs are flattened into per-app configs (with
top-level settings merged in), matching the old pipeline's
``_resolve_apps()`` approach.
"""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

from foundry.engine import Engine
from foundry.operation import get_operation_meta
from foundry.render import RenderCtx
from kiln.generators._env import env
from kiln.renderers import registry
from kiln.renderers.assembler import assemble

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.spec import GeneratedFile

ENTRY_POINT_GROUP = "kiln.operations"


def generate(config: BaseModel) -> list[GeneratedFile]:
    """Generate all files from a kiln config.

    Uses the new engine-based pipeline:

    1. Discover operations from entry points.
    2. Run the engine's build phase (per-app for multi-app).
    3. Assemble build outputs into files.

    Args:
        config: The validated kiln configuration.

    Returns:
        Flat list of all generated files.

    """
    operations = _discover_operations()
    pkg = getattr(config, "package_prefix", "")

    # Project-scoped operations run on the root config.
    project_ops = [op for op in operations if _op_scope(op) == "project"]
    engine = Engine(operations=project_ops, package_prefix=pkg)
    store = engine.build(config)
    ctx = RenderCtx(
        env=env,
        config=config,
        package_prefix=pkg,
    )
    files: list[GeneratedFile] = list(
        assemble(store, registry, ctx),
    )

    # Per-app operations: flatten multi-app structure.
    app_ops = [op for op in operations if _op_scope(op) != "project"]
    for app_cfg in _resolve_apps(config):
        engine = Engine(operations=app_ops, package_prefix=pkg)
        store = engine.build(app_cfg)
        app_ctx = RenderCtx(
            env=env,
            config=app_cfg,
            package_prefix=pkg,
        )
        files.extend(assemble(store, registry, app_ctx))

    return files


def _op_scope(op_cls: type) -> str:
    """Return the scope name for an operation class."""
    meta = get_operation_meta(op_cls)
    return meta.scope if meta else ""


def _resolve_apps(
    config: BaseModel,
) -> list[BaseModel]:
    """Flatten multi-app configs into per-app configs.

    For single-app configs (resources at root level), returns
    ``[config]``.  For multi-app, merges top-level settings
    (auth, databases, operations) into each app sub-config.

    Args:
        config: The top-level project config.

    Returns:
        List of per-app configs, each with resources.

    """
    apps = getattr(config, "apps", [])
    if apps:
        result: list[BaseModel] = []
        for app_ref in apps:
            app_cfg = getattr(app_ref, "config", None)
            if app_cfg is None:
                continue
            # Merge top-level settings into app config
            ops = getattr(app_cfg, "operations", None)
            if ops is None:
                ops = getattr(config, "operations", None)
            merged = app_cfg.model_copy(
                update={
                    "auth": getattr(config, "auth", None),
                    "databases": getattr(
                        config,
                        "databases",
                        [],
                    ),
                    "operations": ops,
                }
            )
            result.append(merged)
        return result

    resources = getattr(config, "resources", [])
    if resources:
        return [config]
    return []


def _discover_operations() -> list[type]:
    """Load operation classes from the entry point group.

    Returns:
        List of operation classes.

    """
    ops: list[type] = []
    eps = importlib.metadata.entry_points(
        group=ENTRY_POINT_GROUP,
    )
    for ep in eps:
        cls = ep.load()
        ops.append(cls)
    return ops
