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

from kiln.config.schema import KilnConfig

_STDLIB_DIR = Path(__file__).parent.parent / "stdlib"


def load(config_path: Path) -> KilnConfig:
    """Load and validate a kiln config file.

    Args:
        config_path: Path to a ``.json`` or ``.jsonnet`` file.

    Returns:
        Validated :class:`~kiln.config.schema.KilnConfig`.

    Raises:
        ValueError: If the file extension is not supported.
        pydantic.ValidationError: If the config fails validation.

    """
    suffix = config_path.suffix.lower()
    if suffix == ".jsonnet":
        raw = _evaluate_jsonnet(config_path)
    elif suffix == ".json":
        raw = config_path.read_text()
    else:
        msg = f"Unsupported config format: {suffix!r} (use .json or .jsonnet)"
        raise ValueError(msg)

    data: dict[str, Any] = json.loads(raw)
    return KilnConfig.model_validate(data)


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

    """
    return _jsonnet.evaluate_file(
        str(path),
        import_callback=_import_callback,
    )
