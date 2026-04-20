"""kiln_core -- generic Python code-generation primitives.

Provides the building blocks for constructing code-generation
tools: mutable file specs, import management, naming helpers,
Jinja2 environment setup, and file output.

Typical usage::

    from kiln_core import (
        FileSpec,
        GeneratedFile,
        ImportCollector,
        Name,
        create_jinja_env,
        render_snippet,
        write_files,
    )
"""

from kiln_core.env import create_jinja_env, render_snippet
from kiln_core.imports import ImportCollector
from kiln_core.naming import Name, prefix_import, split_dotted_class
from kiln_core.output import write_files
from kiln_core.spec import FileSpec, GeneratedFile, wire_exports

__all__ = [
    "FileSpec",
    "GeneratedFile",
    "ImportCollector",
    "Name",
    "create_jinja_env",
    "prefix_import",
    "render_snippet",
    "split_dotted_class",
    "wire_exports",
    "write_files",
]
