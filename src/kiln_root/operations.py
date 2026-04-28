"""Project-scope operation that emits the kiln_root scaffolding.

A single :class:`RootScaffold` op runs at the project scope and
yields one :class:`~foundry.outputs.StaticFile` per file in the
template tree.  Because the ``kiln_root`` :class:`~foundry.target.Target`
points its ``registry`` at :data:`REGISTRY` (and not at the shared
default registry kiln uses), this op only fires when the user
runs ``foundry generate --target kiln_root``.
"""

from typing import TYPE_CHECKING

from foundry.operation import OperationRegistry, operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from foundry.engine import BuildContext
    from kiln_root.config import RootConfig


REGISTRY = OperationRegistry()
"""Per-target registry the kiln_root :class:`~foundry.target.Target`
hands to the engine.  Decorating with ``registry=REGISTRY`` keeps
the kiln_root op out of kiln's default registry so the two targets
never interfere."""


@operation("root_scaffold", scope="project", registry=REGISTRY)
class RootScaffold:
    """Emit the bootstrap files for a fresh kiln project.

    Each file is a :class:`~foundry.outputs.StaticFile` whose
    template lives under :mod:`kiln_root.templates`.  Templates
    interpolate :class:`~kiln_root.config.RootConfig` fields
    (``name``, ``module``, ``description``) and reference the
    target's ``package_prefix`` so the generated ``project.jsonnet``
    points kiln at the same output directory the bootstrap's
    ``justfile`` does (``_generated`` by default).
    """

    def build(
        self,
        ctx: BuildContext[RootConfig, RootConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Yield one :class:`~foundry.outputs.StaticFile` per bootstrap file.

        Args:
            ctx: Build context carrying the validated
                :class:`~kiln_root.config.RootConfig`.
            _options: Unused (no per-op options).

        Yields:
            :class:`~foundry.outputs.StaticFile` objects covering
            the project-root files (``main.py``, ``pyproject.toml``,
            ``justfile``, dotfiles), the kiln config skeleton under
            ``config/``, and an empty ``{module}/`` package.

        """
        config = ctx.instance
        package_prefix = ctx.package_prefix or "_generated"

        # ``kiln-generator`` carries optional extras matching the
        # toggles on RootConfig.  The pyproject template wants a
        # joined list (or an empty list when nothing is enabled,
        # in which case it emits the bare ``"kiln-generator"``
        # line).  Order is fixed for deterministic output.
        kiln_extras = [
            extra
            for flag, extra in (
                (config.opentelemetry, "opentelemetry"),
                (config.files, "files"),
            )
            if flag
        ]

        ctx_vars: dict[str, object] = {
            "name": config.name,
            "module": config.module,
            "description": config.description,
            "package_prefix": package_prefix,
            "opentelemetry": config.opentelemetry,
            "files": config.files,
            "psycopg": config.psycopg,
            "pgcraft": config.pgcraft,
            "pgqueuer": config.pgqueuer,
            "editable": config.editable,
            "kiln_extras": kiln_extras,
        }

        # Every bootstrap file is ``if_exists="skip"`` so ``just
        # bootstrap`` (and any direct re-run of ``foundry generate
        # --target kiln_root``) is non-destructive: users almost
        # always edit these files post-bootstrap and a re-run that
        # quietly resets pyproject.toml or main.py would be a
        # nasty foot-gun.  ``--force`` / ``--force-paths`` on the
        # CLI is the explicit opt-in to clobber.
        yield StaticFile(
            path="main.py",
            template="main.py.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path="pyproject.toml",
            template="pyproject.toml.j2",
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
            path=".gitignore",
            template="gitignore.j2",
            context={},
            if_exists="skip",
        )
        yield StaticFile(
            path=".python-version",
            template="python-version.j2",
            context={},
            if_exists="skip",
        )
        yield StaticFile(
            path="config/project.jsonnet",
            template="config/project.jsonnet.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path=f"config/{config.module}.jsonnet",
            template="config/app.jsonnet.j2",
            context=ctx_vars,
            if_exists="skip",
        )
        yield StaticFile(
            path=f"{config.module}/__init__.py",
            template="",
            context={},
            if_exists="skip",
        )
