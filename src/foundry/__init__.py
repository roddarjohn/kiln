"""foundry -- generic code-generation engine.

Target-agnostic primitives for building code generators:

* :class:`~foundry.engine.Engine` -- walks the config tree and
  runs operations, scope by scope.
* :func:`~foundry.operation.operation` -- decorator that turns a
  class into a scoped build step.
* :mod:`~foundry.outputs` -- typed, mutable dataclasses produced
  by operations (``RouteHandler``, ``SchemaClass``, ``StaticFile``,
  …).
* :class:`~foundry.render.RenderRegistry` -- maps output types to
  renderer functions.
* :class:`~foundry.render.BuildStore` -- accumulator the engine
  and operations read and mutate.

``foundry`` knows nothing about FastAPI.  The :mod:`kiln` package
layers a concrete FastAPI / SQLAlchemy generator on top of it.
"""

from foundry.engine import BuildContext, Engine
from foundry.env import create_jinja_env, render_snippet
from foundry.errors import CLIError
from foundry.imports import ImportCollector
from foundry.naming import Name, prefix_import, split_dotted_class
from foundry.operation import (
    EmptyOptions,
    OperationMeta,
    discover_operations,
    get_operation_meta,
    operation,
    topological_sort,
)
from foundry.output import write_files
from foundry.outputs import (
    EnumClass,
    Field,
    RouteHandler,
    RouteParam,
    RouterMount,
    SchemaClass,
    SerializerFn,
    StaticFile,
    TestCase,
)
from foundry.render import BuildStore, RenderCtx, RenderRegistry
from foundry.scope import PROJECT, Scope, discover_scopes
from foundry.spec import FileSpec, GeneratedFile, wire_exports
from foundry.target import Target, discover_targets

__all__ = [
    "PROJECT",
    "BuildContext",
    "BuildStore",
    "CLIError",
    "EmptyOptions",
    "Engine",
    "EnumClass",
    "Field",
    "FileSpec",
    "GeneratedFile",
    "ImportCollector",
    "Name",
    "OperationMeta",
    "RenderCtx",
    "RenderRegistry",
    "RouteHandler",
    "RouteParam",
    "RouterMount",
    "SchemaClass",
    "Scope",
    "SerializerFn",
    "StaticFile",
    "Target",
    "TestCase",
    "create_jinja_env",
    "discover_operations",
    "discover_scopes",
    "discover_targets",
    "get_operation_meta",
    "operation",
    "prefix_import",
    "render_snippet",
    "split_dotted_class",
    "topological_sort",
    "wire_exports",
    "write_files",
]
