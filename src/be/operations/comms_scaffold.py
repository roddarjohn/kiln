"""Communication-platform scaffold operation.

Generates ``comms.py`` in the project's output tree when
:attr:`~be.config.schema.ProjectConfig.comms` is set.  The file
exposes:

* ``registry`` -- a :class:`ingot.comms.CommRegistry` populated from
  the declared :class:`~be.config.schema.CommTypeConfig` entries.
* ``send_comm`` -- thin wrapper over
  :func:`ingot.comms.send_communication` with the message /
  recipient ORM classes and the registry pre-bound.
* ``dispatch`` -- the worker-side handler built via
  :func:`ingot.comms.make_dispatch_entrypoint`; the consumer
  registers it under :data:`ingot.comms.DISPATCH_ENTRYPOINT` on
  their pgqueuer instance.

No HTTP routes are emitted -- triggering a send is the consumer's
own concern.

Mirrors :class:`~be.operations.rate_limit_scaffold.RateLimitScaffold`
in shape (project-scope, gated by a :meth:`when` predicate so a
project without ``comms`` produces zero references to it).
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from foundry.naming import prefix_import
from foundry.operation import operation
from foundry.outputs import StaticFile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

    from be.config.schema import ProjectConfig, TemplateLike
    from foundry.engine import BuildContext


def _resolve_template(source: TemplateLike, *, kind: str, name: str) -> str:
    """Return the template source text for *source*.

    Bare strings pass through unchanged.  A :class:`TemplateSource`
    is read from disk relative to the current working directory at
    generate time; a missing file raises :class:`FileNotFoundError`
    with a message naming both the comm-type and the missing path
    so the broken spec is easy to fix.

    Args:
        source: The configured template -- inline string or
            :class:`TemplateSource`.
        kind: ``"subject"`` or ``"body"``; surfaces in the error
            message so the caller can tell which field broke.
        name: The comm-type name, also surfaced in the error
            message.

    Returns:
        Template source as a string.

    Raises:
        FileNotFoundError: If *source* is a :class:`TemplateSource`
            and the resolved path does not exist.

    """
    if isinstance(source, str):
        return source

    path = Path(source.path)

    try:
        return path.read_text(encoding="utf-8")

    except FileNotFoundError as exc:
        msg = f"comm type {name!r}: {kind} template file not found at {path!s}"
        raise FileNotFoundError(msg) from exc


def _split(dotted: str) -> tuple[str, str]:
    """Split ``"pkg.mod.Name"`` into ``("pkg.mod", "Name")``.

    Centralises the rsplit so every dotted path on the config
    surface goes through one path -- a missing dot raises a
    ``ValueError`` from ``rsplit`` rather than silently producing
    an unimportable module name.
    """
    if "." not in dotted:
        msg = f"expected dotted path, got {dotted!r}"
        raise ValueError(msg)

    return tuple(dotted.rsplit(".", 1))  # type: ignore[return-value]


@operation("comms_scaffold", scope="project")
class CommsScaffold:
    """Generate ``comms.py``.

    A single :class:`~foundry.outputs.StaticFile` carrying the
    registry, the producer wrapper, and the worker dispatch handler.
    The file is the only place the platform's wiring lives: the
    consumer's request handlers and worker bootstrap import from it
    rather than constructing the registry themselves.
    """

    def when(self, ctx: BuildContext[ProjectConfig, ProjectConfig]) -> bool:
        """Run only when ``ctx.instance.comms`` is set.

        Args:
            ctx: Build context with project config.

        Returns:
            ``True`` when the project opts into the comms platform.

        """
        return bool(ctx.instance.comms)

    def build(
        self,
        ctx: BuildContext[ProjectConfig, ProjectConfig],
        _options: BaseModel,
    ) -> Iterable[StaticFile]:
        """Produce the comms wiring module.

        Args:
            ctx: Build context.  ``when`` has already confirmed
                ``ctx.instance.comms`` is not ``None``.
            _options: Unused.

        Yields:
            One :class:`~foundry.outputs.StaticFile` for ``comms.py``.

        """
        config = ctx.instance
        comms = config.comms
        assert comms is not None  # noqa: S101 -- guaranteed by when()

        message_module, message_class = _split(comms.message_model)
        recipient_module, recipient_class = _split(comms.recipient_model)

        # Per-type context-schema imports.  Two types whose context
        # schemas live in the same module share a single ``from x
        # import A, B`` line in the generated file; the template
        # groups by module.
        types_ctx: list[dict[str, Any]] = []

        for entry in comms.types:
            ctx_module, ctx_class = _split(entry.context_schema)
            types_ctx.append(
                {
                    "name": entry.name,
                    "context_module": ctx_module,
                    "context_class": ctx_class,
                    "subject_template": _resolve_template(
                        entry.subject_template,
                        kind="subject",
                        name=entry.name,
                    ),
                    "body_template": _resolve_template(
                        entry.body_template,
                        kind="body",
                        name=entry.name,
                    ),
                    "default_methods": list(entry.default_methods),
                },
            )

        transports_ctx: list[dict[str, str]] = []

        for method, dotted in comms.transports.items():
            mod, name = _split(dotted)
            transports_ctx.append(
                {"method": method, "module": mod, "name": name},
            )

        renderer_module: str | None = None
        renderer_name: str | None = None

        if comms.renderer is not None:
            renderer_module, renderer_name = _split(comms.renderer)

        preferences_module: str | None = None
        preferences_name: str | None = None

        if comms.preferences is not None:
            preferences_module, preferences_name = _split(comms.preferences)

        database = config.resolve_database(comms.db_key)

        comms_module = prefix_import(config.package_prefix, "comms")
        session_module = prefix_import(
            config.package_prefix,
            database.session_module,
        )

        yield StaticFile(
            path="comms.py",
            template="init/comms_setup.py.j2",
            context={
                "comms_module": comms_module,
                "message_module": message_module,
                "message_class": message_class,
                "recipient_module": recipient_module,
                "recipient_class": recipient_class,
                "types": types_ctx,
                "transports": transports_ctx,
                "renderer_module": renderer_module,
                "renderer_name": renderer_name,
                "preferences_module": preferences_module,
                "preferences_name": preferences_name,
                "session_module": session_module,
                "db_key": database.key,
            },
        )
