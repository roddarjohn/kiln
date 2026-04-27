"""Thin wrapper around ``_jsonnet`` with prefix-based stdlib imports.

Jsonnet delegates ``import`` resolution to a host-supplied callback.
This module provides a callback that interprets the first path
segment of an import as a registered *stdlib prefix*: given
``{"kiln": Path(".../jsonnet")}``, an import like
``'kiln/auth/jwt.libsonnet'`` resolves under that directory.
Imports without a matching prefix fall through to the normal
relative-to-importer resolution, so user configs can still
``import './shared.libsonnet'`` freely.

The only public entry points are :func:`evaluate` (file in, JSON
string out) and :func:`make_import_callback` (the bare callback,
exposed for callers that want to build their own evaluator).
"""

from pathlib import Path
from typing import TYPE_CHECKING

import _jsonnet

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def evaluate(
    path: Path,
    stdlibs: Mapping[str, Path] | None = None,
) -> str:
    """Evaluate a Jsonnet file to a JSON string.

    Args:
        path: Path to the ``.jsonnet`` file.
        stdlibs: Optional mapping of jsonnet import prefix to
            stdlib directory.  ``{"kiln": Path(".../jsonnet")}``
            makes ``import 'kiln/...'`` resolve under that
            directory.

    Returns:
        JSON string produced by the Jsonnet evaluator.

    Raises:
        RuntimeError: If Jsonnet evaluation fails.
        OSError: If the file cannot be read.

    """
    return _jsonnet.evaluate_file(
        str(path),
        import_callback=make_import_callback(stdlibs or {}),
    )


def make_import_callback(
    stdlibs: Mapping[str, Path],
) -> Callable[[str, str], tuple[str, bytes]]:
    """Build a jsonnet import callback that honours *stdlibs*.

    Imports whose first path segment matches a registered prefix
    are resolved from the associated stdlib directory; all other
    imports are resolved relative to the importing file.

    Args:
        stdlibs: Mapping of import prefix to stdlib directory.

    Returns:
        Callable with the ``_jsonnet`` import-callback signature
        ``(importing_dir, import_path) -> (resolved_path, bytes)``.

    """

    def _callback(importing_dir: str, import_path: str) -> tuple[str, bytes]:
        prefix, _, rest = import_path.partition("/")

        if rest and prefix in stdlibs:
            target = stdlibs[prefix] / rest

        else:
            target = Path(importing_dir) / import_path

        return str(target), target.read_bytes()

    return _callback
