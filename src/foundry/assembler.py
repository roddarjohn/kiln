"""Assembler: combine fragments into output files.

Each dispatched render gets a :class:`~foundry.render.RenderCtx`
with ``store`` and ``instance_id`` set to the current entry.
Renderers yield a :class:`~foundry.render.FileFragment` (declaring
the output file and its wrapper template) plus one or more
:class:`~foundry.render.SnippetFragment` contributions into the
file's slot lists.  This module folds
them: files with the same path merge via ``|``, snippets render
(either from ``value`` or their ``template``), and each file's
wrapper is rendered once with every slot's items in order.
"""

from __future__ import annotations

from dataclasses import replace
from functools import reduce
from itertools import groupby
from operator import attrgetter, or_
from typing import TYPE_CHECKING, Any

from foundry.env import render_template
from foundry.imports import format_imports
from foundry.render import FileFragment, SnippetFragment
from foundry.spec import GeneratedFile

if TYPE_CHECKING:
    from foundry.render import Fragment, RenderCtx, RenderRegistry
    from foundry.store import BuildStore


def assemble(
    store: BuildStore,
    registry: RenderRegistry,
    ctx: RenderCtx,
) -> list[GeneratedFile]:
    """Turn a build store into rendered output files.

    Walks every item in the store, dispatches to the registry to
    collect file/snippet fragments, then renders one file per
    declared shell with its snippets folded in.

    Args:
        store: The build store from the engine's build phase.
        registry: Render registry with all renderers registered.
        ctx: Render context -- env, config, package prefix.

    Returns:
        Flat list of :class:`~foundry.spec.GeneratedFile` objects
        ready for output.

    """
    ctx = replace(ctx, store=store)
    fragments: list[Fragment] = []

    for instance_id, _, items in store.entries():
        dispatch_ctx = replace(ctx, instance_id=instance_id)
        fragments.extend(
            fragment
            for item in items
            for fragment in registry.render(obj=item, ctx=dispatch_ctx)
        )

    return _assemble_files(fragments=fragments, ctx=ctx)


def _assemble_files(
    fragments: list[Fragment],
    ctx: RenderCtx,
) -> list[GeneratedFile]:
    """Partition by type, then render one file per declared path.

    FileFragments at the same path are merged via ``|``, which
    raises on template/context disagreement.  Snippets whose
    path has no matching FileFragment also raise.
    """
    files = [frag for frag in fragments if isinstance(frag, FileFragment)]
    snippets = [frag for frag in fragments if isinstance(frag, SnippetFragment)]
    files_by_path = _group_by_path(fragments=files)
    snippets_by_path = _group_by_path(fragments=snippets)

    orphan_paths = snippets_by_path.keys() - files_by_path.keys()

    if orphan_paths:
        msg = (
            "SnippetFragment targets path with no FileFragment: "
            f"{sorted(orphan_paths)}"
        )
        raise ValueError(msg)

    return [
        _render_file(
            file=reduce(or_, group),
            snippets=snippets_by_path.get(path, ()),
            ctx=ctx,
        )
        for path, group in files_by_path.items()
    ]


def _group_by_path[T: (FileFragment, SnippetFragment)](
    fragments: list[T],
) -> dict[str, list[T]]:
    """Bucket *fragments* by their ``path`` attribute."""
    _path_of = attrgetter("path")

    ordered = sorted(fragments, key=_path_of)
    return {path: list(group) for path, group in groupby(ordered, key=_path_of)}


def _render_file(
    file: FileFragment,
    snippets: list[SnippetFragment] | tuple[SnippetFragment, ...],
    ctx: RenderCtx,
) -> GeneratedFile:
    """Render *file* with *snippets* folded into its slot lists.

    A blank :attr:`FileFragment.template` produces empty content
    (convention for empty files like ``__init__.py``).
    """
    if not file.template:
        return GeneratedFile(path=file.path, content="")

    imports = file.imports
    slots: dict[str, list[Any]] = {}

    for snippet in snippets:
        imports = imports | snippet.imports
        slots.setdefault(snippet.slot, []).append(
            snippet.render_slot_item(env=ctx.env)
        )

    context: dict[str, Any] = {
        **file.context,
        **slots,
        "import_block": format_imports(
            collector=imports, language=ctx.language
        ),
    }

    rendered = render_template(
        env=ctx.env,
        template_name=file.template,
        **context,
    )

    return GeneratedFile(path=file.path, content=rendered.rstrip() + "\n")
