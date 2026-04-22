"""Assembler: combine fragments into output files.

Each dispatched render gets a :class:`RenderCtx` with ``store``
and ``instance_id`` set to the current entry.  Renderers yield a
:class:`FileFragment` (declaring the output file and its
wrapper template) plus one or more :class:`SnippetFragment`
contributions into the file's slot lists.  This module folds
them: files with the same path merge, snippets render (either
from ``value`` or their ``template``), and each file's wrapper
is rendered once with every slot's items in order.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from foundry.imports import ImportCollector
from foundry.render import FileFragment, SnippetFragment
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
    collect shell/snippet fragments, then renders one file per
    declared shell with its snippets folded in.

    Args:
        store: The build store from the engine's build phase.
        registry: Render registry with all renderers registered.
        ctx: Render context -- env, config, package prefix.

    Returns:
        Flat list of :class:`GeneratedFile` objects ready for
        output.

    """
    ctx = replace(ctx, store=store)
    fragments: list[Fragment] = []

    for instance_id, _, items in store.entries():
        dispatch_ctx = replace(ctx, instance_id=instance_id)
        fragments.extend(
            fragment
            for item in items
            for fragment in registry.render(item, dispatch_ctx)
        )

    return _assemble_files(fragments, ctx)


def _assemble_files(
    fragments: list[Fragment],
    ctx: RenderCtx,
) -> list[GeneratedFile]:
    """Fold *fragments* into one :class:`GeneratedFile` per file.

    Drops fragments with an empty ``path``.  Raises
    :class:`ValueError` for orphan snippets (no FileFragment at
    the same path) and for FileFragment disagreements
    (different templates or conflicting context values).
    """
    files: dict[str, FileFragment] = {}
    snippets: dict[str, list[SnippetFragment]] = {}

    for frag in fragments:
        if not frag.path:
            continue
        if isinstance(frag, FileFragment):
            existing = files.get(frag.path)
            files[frag.path] = (
                _merge_files(existing, frag) if existing else frag
            )
        else:
            snippets.setdefault(frag.path, []).append(frag)

    orphans = set(snippets) - set(files)
    if orphans:
        msg = (
            "SnippetFragment targets path with no FileFragment: "
            f"{sorted(orphans)}"
        )
        raise ValueError(msg)

    return [
        _render_file(file, snippets.get(file.path, ()), ctx)
        for file in files.values()
    ]


def _merge_files(first: FileFragment, other: FileFragment) -> FileFragment:
    """Combine two FileFragments targeting the same path.

    The wrapper template must match; conflicting context values
    for a shared key are a programming error.  Imports union.
    """
    if first.template != other.template:
        msg = (
            f"FileFragment template mismatch at {first.path!r}: "
            f"{first.template!r} vs {other.template!r}"
        )
        raise ValueError(msg)

    merged_ctx = dict(first.context)
    for key, value in other.context.items():
        if key in merged_ctx and merged_ctx[key] != value:
            msg = (
                f"FileFragment context conflict at {first.path!r} "
                f"for {key!r}: {merged_ctx[key]!r} vs {value!r}"
            )
            raise ValueError(msg)
        merged_ctx[key] = value

    imports = ImportCollector()
    imports.update(first.imports)
    imports.update(other.imports)

    return FileFragment(
        path=first.path,
        template=first.template,
        context=merged_ctx,
        imports=imports,
    )


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

    imports = ImportCollector()
    imports.update(file.imports)

    slots: dict[str, list[Any]] = {}
    for snippet in snippets:
        imports.update(snippet.imports)
        slots.setdefault(snippet.slot, []).append(_slot_item(snippet, ctx))

    context: dict[str, Any] = {
        **file.context,
        **slots,
        "import_block": imports.block(),
    }
    tmpl = ctx.env.get_template(file.template)
    content = tmpl.render(**context).rstrip() + "\n"
    return GeneratedFile(path=file.path, content=content)


def _slot_item(snippet: SnippetFragment, ctx: RenderCtx) -> object:
    """Produce the slot-list item a *snippet* contributes.

    ``template`` is rendered with ``context`` (yielding a
    string); otherwise ``value`` is passed through as-is so the
    wrapper template can iterate over structured data.
    """
    if snippet.template is not None:
        return ctx.env.get_template(snippet.template).render(**snippet.context)
    return snippet.value
