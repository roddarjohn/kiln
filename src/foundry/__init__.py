"""foundry -- generic code-generation engine.

Target-agnostic primitives for building code generators:

* :class:`~foundry.engine.Engine` -- walks the config tree and
  runs operations, scope by scope.
* :func:`~foundry.operation.operation` -- decorator that turns a
  class into a scoped build step.
* :mod:`~foundry.outputs` -- :class:`~foundry.outputs.StaticFile`, the one build
  output that's target-neutral.  Python / FastAPI-specific
  outputs (``RouteHandler``, ``SchemaClass``, …) live in
  :mod:`be.operations.outputs`.
* :class:`~foundry.render.RenderRegistry` -- maps output types to
  renderer functions.
* :class:`~foundry.render.BuildStore` -- accumulator the engine
  and operations read and mutate.

``foundry`` knows nothing about FastAPI.  The :mod:`be` package
layers a concrete FastAPI / SQLAlchemy generator on top of it.
"""

from foundry.config import FoundryConfig, load_config
from foundry.engine import BuildContext, Engine
from foundry.env import create_jinja_env, render_template
from foundry.errors import CLIError, ConfigError, GenerationError
from foundry.imports import ImportCollector
from foundry.naming import Name, prefix_import, split_dotted_class
from foundry.operation import EmptyOptions, OperationMeta, operation
from foundry.output import write_files
from foundry.outputs import StaticFile
from foundry.pipeline import generate
from foundry.render import RenderCtx, RenderRegistry
from foundry.scope import PROJECT, Scope, Scoped, discover_scopes
from foundry.spec import GeneratedFile
from foundry.store import BuildStore
from foundry.target import Target, discover_targets

__all__ = [
    "PROJECT",
    "BuildContext",
    "BuildStore",
    "CLIError",
    "ConfigError",
    "EmptyOptions",
    "Engine",
    "FoundryConfig",
    "GeneratedFile",
    "GenerationError",
    "ImportCollector",
    "Name",
    "OperationMeta",
    "RenderCtx",
    "RenderRegistry",
    "Scope",
    "Scoped",
    "StaticFile",
    "Target",
    "create_jinja_env",
    "discover_scopes",
    "discover_targets",
    "generate",
    "load_config",
    "operation",
    "prefix_import",
    "render_template",
    "split_dotted_class",
    "write_files",
]
