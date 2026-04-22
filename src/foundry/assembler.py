"""Assembler: merge fragments into output files.

The assembler is a dumb merge loop.  For every item in the build
store, ask the registry for its fragments; group fragments by
output path; union their imports; concatenate list-valued
shell-context entries (first-seen wins for scalars); then render
the shell template.  All framework- or file-specific knowledge
lives in the renderers, not here.

For each store entry, the assembler looks up the scope instance
that produced it (via :meth:`BuildStore.get_instance`) and
exposes it on ``RenderCtx.extras`` under the scope name.  Kiln
renderers read e.g. ``ctx.extras["resource"]`` to derive paths
and imports from the resource config.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from foundry.imports import ImportCollector
from foundry.spec import GeneratedFile

if TYPE_CHECKING:
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

    for instance_id, _, items in store.entries():
        scoped = _scoped_ctx(ctx, store, instance_id)

        for item in items:
            # Missing renderer = silent data loss.  Let
            # registry.render raise :class:`LookupError`; the
            # pipeline wraps it in :class:`GenerationError`.
            fragments.extend(registry.render(item, scoped))

    return _merge_fragments(fragments, ctx)


def _scoped_ctx(
    ctx: RenderCtx,
    store: BuildStore,
    instance_id: str,
) -> RenderCtx:
    """Return a :class:`RenderCtx` carrying every ancestor instance.

    Attaches the current scope instance and every ancestor (keyed
    by scope name) to ``ctx.extras``, so a renderer at
    ``operation`` scope can read ``ctx.extras["resource"]``,
    ``ctx.extras["app"]`` etc. without walking the store itself.
    """
    extras = dict(ctx.extras)

    instance = store.get_instance(instance_id)
    if instance is not None:
        extras[store.scope_of(instance_id).name] = instance

    for scope in store.scope_tree:
        if scope.name in extras or scope.name == "project":
            continue
        ancestor = store.ancestor_of(instance_id, scope.name)
        if ancestor is not None:
            extras[scope.name] = ancestor

    return replace(ctx, extras=extras)


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
