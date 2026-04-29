"""Project-scope operation that emits the be_root scaffolding.

A single :class:`RootScaffold` op runs at the project scope and
yields one :class:`~foundry.outputs.StaticFile` per file in the
template tree.  The class is registered under the
``be_root.operations`` entry-point group (see
``pyproject.toml``); foundry walks that group at build time when
the user runs ``foundry generate --target be_root``, keeping
be_root's ops out of every other target's per-build registry.
"""

from typing import TYPE_CHECKING

from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be_root.config import RootConfig
    from foundry.engine import BuildContext


@operation("root_scaffold", scope="project")
class RootScaffold:
    """Emit the bootstrap files for a fresh be project.

    Each file is a :class:`~foundry.outputs.StaticFile` whose
    template lives under ``be_root/templates/``.  Templates
    interpolate :class:`~be_root.config.RootConfig` fields
    (``name``, ``module``, ``description``) and reference the
    target's ``package_prefix`` so the generated ``project.jsonnet``
    points be at the same output directory the bootstrap's
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
                :class:`~be_root.config.RootConfig`.
            _options: Unused (no per-op options).

        Yields:
            :class:`~foundry.outputs.StaticFile` objects covering
            the project-root files (``main.py``, ``pyproject.toml``,
            ``justfile``, dotfiles), the be config skeleton under
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
            "auth": config.auth,
            "psycopg": config.psycopg,
            "pgcraft": config.pgcraft,
            "pgqueuer": config.pgqueuer,
            "editable": config.editable,
            "kiln_extras": kiln_extras,
        }

        # Every bootstrap file is ``if_exists="skip"`` so ``just
        # bootstrap`` (and any direct re-run of ``foundry generate
        # --target be_root``) is non-destructive: users almost
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

        if config.auth:
            yield StaticFile(
                path="auth.py",
                template="auth.py.j2",
                context={},
                if_exists="skip",
            )

        # Alembic only matters when pgcraft is in play -- without
        # pgcraft, ``Base.metadata.create_all`` (or none at all)
        # is enough.  With pgcraft, autogenerate is the only way
        # to materialize the views, INSTEAD OF triggers, and
        # functions the runtime queries.
        if config.pgcraft:
            yield StaticFile(
                path="alembic.ini",
                template="alembic.ini.j2",
                context=ctx_vars,
                if_exists="skip",
            )
            yield StaticFile(
                path="migrations/env.py",
                template="migrations/env.py.j2",
                context=ctx_vars,
                if_exists="skip",
            )
            yield StaticFile(
                path="migrations/script.py.mako",
                template="migrations/script.py.mako",
                context={},
                if_exists="skip",
            )
            # No empty versions/ dir scaffolded -- alembic creates it
            # on the first ``alembic revision`` run.
