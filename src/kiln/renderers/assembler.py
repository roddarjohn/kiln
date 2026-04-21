"""Assembler: merge fragments into output files.

The assembler is a dumb merge loop.  For every item in the build
store, ask the registry for its fragments; group fragments by
output path; union their imports; concatenate list-valued
shell-context entries (first-seen wins for scalars); then render
the shell template.  All framework- or file-specific knowledge
lives in the renderers, not here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from foundry.imports import ImportCollector
from foundry.naming import Name
from foundry.spec import GeneratedFile

if TYPE_CHECKING:
    from pydantic import BaseModel

    from foundry.render import (
        BuildStore,
        Fragment,
        RenderCtx,
        RenderRegistry,
    )


def assemble(
    store: BuildStore,
    registry: RenderRegistry,
    ctx: RenderCtx,
) -> list[GeneratedFile]:
    """Turn a build store into rendered output files.

    Walks every item in the store, dispatches to the registry to
    collect :class:`~foundry.render.Fragment` objects, groups them
    by path, and renders each group's shell template with merged
    imports and context.

    Args:
        store: The build store from the engine's build phase.
        registry: Render registry with all renderers registered.
        ctx: Render context -- env, config, package prefix.

    Returns:
        Flat list of :class:`GeneratedFile` objects ready for
        output.

    """
    fragments: list[Fragment] = []
    for scope_name, instance_id, _op_name, items in store.entries():
        scoped = _scoped_ctx(ctx, scope_name, instance_id)
        for item in items:
            if not registry.has_renderer(type(item)):
                continue
            fragments.extend(registry.render(item, scoped))
    return _merge_fragments(fragments, ctx)


def _scoped_ctx(
    ctx: RenderCtx,
    scope_name: str,
    instance_id: str,
) -> RenderCtx:
    """Return a :class:`RenderCtx` carrying the current scope instance.

    Renderers that care about scope-specific context read it from
    ``ctx.extras``.  For the resource scope, this assembler looks
    up the :class:`~kiln.config.schema.ResourceConfig` whose class
    name matches *instance_id* and attaches it under the
    ``"resource"`` key.  For other scopes (currently just
    ``"project"``) the context is returned unchanged.
    """
    if scope_name == "resource":
        resource = _find_resource(ctx.config, instance_id)
        if resource is not None:
            return replace(ctx, extras={"resource": resource})
    return ctx


def _find_resource(
    config: BaseModel,
    instance_id: str,
) -> BaseModel | None:
    """Locate the :class:`ResourceConfig` matching *instance_id*.

    The engine derives ``instance_id`` from the resource's
    dotted ``model`` path (``class_name.lower()``); we invert that
    lookup here so renderers can fetch the resource without the
    engine having to thread it through.

    Searches the top-level ``resources`` list first, then every
    app's nested ``config.resources`` — a multi-app project has
    no top-level resources, so the nested walk is required.

    Args:
        config: The top-level config passed to the assembler.
        instance_id: The engine's scope-instance identifier.

    Returns:
        Matching resource config, or ``None`` when not found.

    """
    for resource in _iter_all_resources(config):
        model = getattr(resource, "model", "")
        if "." not in model:
            continue
        _, name = Name.from_dotted(model)
        if name.lower == instance_id:
            return resource
    return None


def _iter_all_resources(config: BaseModel) -> list[BaseModel]:
    """Yield every :class:`ResourceConfig` in a project config.

    Resources can live at the root (single-app) or inside each
    ``AppRef.config`` (multi-app); both are yielded here.

    Args:
        config: Top-level project config.

    Returns:
        Flat list of resource configs across all apps.

    """
    resources: list[BaseModel] = list(getattr(config, "resources", []))
    for app_ref in getattr(config, "apps", []):
        app_cfg = getattr(app_ref, "config", None)
        if app_cfg is None:
            continue
        resources.extend(getattr(app_cfg, "resources", []))
    return resources


def _merge_fragments(
    fragments: list[Fragment],
    ctx: RenderCtx,
) -> list[GeneratedFile]:
    """Group *fragments* by path, merge them, and render.

    Merge rules:

    * Imports from every fragment targeting the same path are
      unioned via :meth:`ImportCollector.update`.
    * ``shell_context`` entries are merged key by key.  When two
      fragments set the same list-valued key, the lists are
      concatenated in fragment order; scalar values are
      first-seen-wins.
    * The first fragment's ``shell_template`` is used for the
      group -- all fragments targeting a given path are expected
      to agree on their shell template.
    * A blank ``shell_template`` is a convention for an
      empty-content file (e.g. ``__init__.py``).

    Args:
        fragments: All fragments contributed by renderers.
        ctx: Render context with the Jinja environment.

    Returns:
        One :class:`GeneratedFile` per unique fragment path.

    """
    by_path: dict[str, list[Fragment]] = {}
    for frag in fragments:
        if not frag.path:
            continue
        by_path.setdefault(frag.path, []).append(frag)

    files: list[GeneratedFile] = []
    for path, frags in by_path.items():
        merged_imports = ImportCollector()
        merged_ctx: dict[str, Any] = {}
        shell_template = frags[0].shell_template
        for frag in frags:
            merged_imports.update(frag.imports)
            for key, value in frag.shell_context.items():
                if (
                    key in merged_ctx
                    and isinstance(merged_ctx[key], list)
                    and isinstance(value, list)
                ):
                    merged_ctx[key] = merged_ctx[key] + value
                elif key not in merged_ctx:
                    merged_ctx[key] = value

        if shell_template:
            merged_ctx["import_block"] = merged_imports.block()
            tmpl = ctx.env.get_template(shell_template)
            content = tmpl.render(**merged_ctx).rstrip() + "\n"
        else:
            content = ""

        files.append(GeneratedFile(path=path, content=content))
    return files
