"""Load and validate kiln configuration files.

Supports ``.json`` files directly and ``.jsonnet`` files via the
``jsonnet`` package (``pip install jsonnet``).  Jsonnet imports
prefixed with ``kiln/`` are resolved from kiln's bundled stdlib
(e.g. ``import 'kiln/auth/jwt.libsonnet'``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import _jsonnet
from pydantic import ValidationError

from kiln.config.schema import ProjectConfig
from kiln.errors import ConfigError

_STDLIB_DIR = Path(__file__).parent.parent / "stdlib"


def load(config_path: Path) -> ProjectConfig:
    """Load and validate a kiln config file.

    Args:
        config_path: Path to a ``.json`` or ``.jsonnet`` file.

    Returns:
        Validated :class:`~kiln.config.schema.ProjectConfig`.

    Raises:
        ConfigError: If the file is missing, has an unsupported
            extension, fails to parse, or fails schema validation.

    """
    suffix = config_path.suffix.lower()
    if suffix == ".jsonnet":
        raw = _evaluate_jsonnet(config_path)
    elif suffix == ".json":
        try:
            raw = config_path.read_text()
        except OSError as exc:
            msg = f"Could not read {config_path}: {exc}"
            raise ConfigError(msg) from exc
    else:
        msg = f"Unsupported config format: {suffix!r} (use .json or .jsonnet)"
        raise ConfigError(msg)

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON in {config_path}: {exc}"
        raise ConfigError(msg) from exc

    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        msg = f"Invalid config in {config_path}: {exc}"
        raise ConfigError(msg) from exc


def _import_callback(importing_dir: str, import_path: str) -> tuple[str, bytes]:
    """Resolve import paths during Jsonnet evaluation.

    Maps ``kiln/...`` imports to the bundled stdlib directory;
    all other paths are resolved relative to the importing file.

    Args:
        importing_dir: Directory of the file doing the import.
        import_path: The path string from the import expression.

    Returns:
        ``(resolved_path, file_contents)`` tuple.

    """
    if import_path.startswith("kiln/"):
        target = _STDLIB_DIR / import_path[len("kiln/") :]
    else:
        target = Path(importing_dir) / import_path
    return str(target), target.read_bytes()


def _evaluate_jsonnet(path: Path) -> str:
    """Evaluate a Jsonnet file to a JSON string.

    Args:
        path: Path to the ``.jsonnet`` file.

    Returns:
        JSON string produced by the Jsonnet evaluator.

    Raises:
        ConfigError: If the file is missing or Jsonnet evaluation
            fails.

    """
    try:
        return _jsonnet.evaluate_file(
            str(path),
            import_callback=_import_callback,
        )
    except RuntimeError as exc:
        msg = f"Jsonnet evaluation failed for {path}: {exc}"
        raise ConfigError(msg) from exc
    except OSError as exc:
        msg = f"Could not read {path}: {exc}"
        raise ConfigError(msg) from exc
