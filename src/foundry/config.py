"""Load and validate target config files.

Supports ``.json`` files directly and ``.jsonnet`` files via
:mod:`foundry.jsonnet`, which adds prefix-based stdlib imports so
targets can ship their own libsonnet helpers under a registered
prefix (e.g. ``import 'be/auth/jwt.libsonnet'``).
"""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from foundry import jsonnet
from foundry.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class FoundryConfig(BaseModel):
    """Base class for target-level config schemas.

    Targets registering with foundry should subclass this so their
    config model carries the foundry-recognized meta fields.  Today
    that is just :attr:`package_prefix`; more may be added as
    foundry grows.  Target-specific fields (auth, databases, apps,
    routes, whatever) are declared by the subclass.

    Attributes:
        package_prefix: Dotted prefix prepended to the dotted
            import path of every :class:`~foundry.spec.GeneratedFile`
            (and used as the on-disk directory prefix).  Empty
            string disables the prefix.  Targets that generate
            Python (or another language with package semantics)
            should override the default to something sensible
            (be uses ``"_generated"``).

    """

    package_prefix: str = ""


class ExtensibleConfig(BaseModel):
    """Base for config nodes whose extra keys feed an op's options.

    Targets that dispatch ops by a field value (``name``, ``type``,
    ...) and pass every other key through to the op's own
    ``Options`` model can subclass this to inherit
    ``extra="allow"`` plus the :attr:`options` accessor instead of
    re-spelling them per config class.  be uses it for
    :class:`be.config.schema.OperationConfig` and
    :class:`be.config.schema.ModifierConfig`; other targets that
    need the same dispatch shape can do the same.
    """

    model_config = ConfigDict(extra="allow")

    @property
    def options(self) -> dict[str, Any]:
        """Op-specific options (every key not declared on the model)."""
        return self.model_extra or {}


def load_config(
    path: Path,
    schema: type[FoundryConfig],
    stdlibs: Mapping[str, Path] | None = None,
) -> FoundryConfig:
    """Load and validate a config file against *schema*.

    Args:
        path: Path to a ``.json`` or ``.jsonnet`` file.
        schema: Pydantic model used to validate the parsed data.
        stdlibs: Optional mapping of jsonnet import prefix to
            stdlib directory.  See :func:`foundry.jsonnet.evaluate`.

    Returns:
        Validated model instance of *schema*.

    Raises:
        ConfigError: If the file is missing, has an unsupported
            extension, fails to parse, or fails schema validation.

    """
    raw = _read_source(path, stdlibs or {})

    try:
        return schema.model_validate_json(raw)

    except ValidationError as exc:
        msg = f"Invalid config in {path}: {exc}"
        raise ConfigError(msg) from exc


def _read_source(path: Path, stdlibs: Mapping[str, Path]) -> str:
    """Read *path* as JSON or Jsonnet source, returning JSON text.

    ``.jsonnet`` is evaluated via :func:`foundry.jsonnet.evaluate`
    with *stdlibs* wired in; ``.json`` is returned verbatim.
    """
    suffix = path.suffix.lower()

    try:
        if suffix == ".jsonnet":
            return jsonnet.evaluate(path, stdlibs)

        if suffix == ".json":
            return path.read_text()

    except RuntimeError as exc:
        msg = f"Jsonnet evaluation failed for {path}: {exc}"
        raise ConfigError(msg) from exc

    except OSError as exc:
        msg = f"Could not read {path}: {exc}"
        raise ConfigError(msg) from exc

    msg = f"Unsupported config format: {suffix!r} (use .json or .jsonnet)"
    raise ConfigError(msg)
