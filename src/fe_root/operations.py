"""Project-scope operation that emits the fe_root scaffolding.

A single :class:`RootScaffold` op runs at the project scope and
yields one :class:`~foundry.outputs.StaticFile` per file in the
template tree.  The class is registered under the
``fe_root.operations`` entry-point group (see ``pyproject.toml``);
foundry walks that group at build time when the user runs
``foundry generate --target fe_root``, keeping fe_root's ops out
of every other target's per-build registry.
"""

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from fe_root.config import RootConfig
    from foundry.engine import BuildContext


@operation("root_scaffold", scope="project")
class RootScaffold:
    """Emit the bootstrap files for a fresh fe project.

    Each file is a :class:`~foundry.outputs.StaticFile` whose
    template lives under :mod:`fe_root.templates`.  Templates
    interpolate :class:`~fe_root.config.RootConfig` fields
    (``name``, ``description``, ``glaze``, ``editable``,
    ``openapi_spec``).
    """

    def build(
        self,
        ctx: BuildContext[RootConfig, RootConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield one :class:`~foundry.outputs.StaticFile` per bootstrap file.

        Args:
            ctx: Build context carrying the validated
                :class:`~fe_root.config.RootConfig`.
            _options: Unused (no per-op options).

        Yields:
            :class:`~foundry.outputs.StaticFile` objects covering
            the project-root files (``package.json``, ``justfile``,
            ``tsconfig.json``, ``vite.config.ts``, ``index.html``,
            dotfiles), the ``src/`` entry points, and a starter
            ``config/fe.jsonnet`` for the ``fe`` target.

        """
        config = ctx.instance

        ctx_vars: dict[str, object] = {
            "name": config.name,
            "description": config.description,
            "glaze": config.glaze,
            "editable": config.editable,
            "openapi_spec": config.openapi_spec,
        }

        # Every bootstrap file is ``if_exists="skip"`` so a
        # re-bootstrap (``just bootstrap`` or a direct
        # ``foundry generate --target fe_root``) is non-destructive
        # by default.  The user almost always edits ``src/App.tsx``,
        # ``package.json``, etc. -- a re-run that quietly resets
        # them would be a nasty foot-gun.  ``--force`` /
        # ``--force-paths`` is the explicit opt-in to clobber.
        yield StaticFile(
            path="package.json",
            template="package.json.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="justfile",
            template="justfile.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="tsconfig.json",
            template="tsconfig.json.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="vite.config.ts",
            template="vite.config.ts.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="index.html",
            template="index.html.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="src/main.tsx",
            template="src/main.tsx.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="src/App.tsx",
            template="src/App.tsx.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="src/index.css",
            template="src/index.css.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path=".gitignore",
            template="gitignore.j2",
            context={},
            if_exists="skip",
        )
        yield StaticFile(
            path=".nvmrc",
            template="nvmrc.j2",
            context={},
            if_exists="skip",
        )
        yield StaticFile(
            path="config/fe.jsonnet",
            template="config/fe.jsonnet.j2",
            context=ctx_vars,
            if_exists="skip",
        )
