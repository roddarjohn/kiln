"""Main generation entry point.

Config in, files out.  Registered :class:`EntryType` instances
drive generation: each entry type extracts entries from the
config, generates files per entry, runs per-app post-hooks,
and optionally contributes project-level scaffold or routing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kiln.generators.fastapi.pipeline import generate_resource
from kiln.generators.fastapi.project_router import (
    generate_project_router,
)
from kiln.generators.fastapi.router import generate_app_router
from kiln.generators.fastapi.utils_gen import generate_utils
from kiln.generators.init.scaffold import generate_scaffold

if TYPE_CHECKING:
    from kiln.config.schema import KilnConfig, ResourceConfig
    from kiln_core import GeneratedFile


# -------------------------------------------------------------------
# EntryType protocol
# -------------------------------------------------------------------


@runtime_checkable
class EntryType(Protocol):
    """A discoverable unit of generation.

    Each entry type knows how to:

    - **before_apps**: emit project-level scaffold files
      (auth, db infrastructure) before any app is processed.
    - **entries / generate_one / after_all**: per-app generation
      — extract entries, generate per entry, then run post-hooks.
    - **after_apps**: emit project-level files (e.g. a root
      router) after all apps are processed.

    Hooks that have nothing to contribute return an empty list.
    """

    def before_apps(
        self,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Run once before any app is processed.

        Use this for project-level infrastructure that does not
        depend on per-app entries (e.g. auth scaffold, database
        sessions).
        """
        ...

    def entries(
        self,
        config: KilnConfig,
    ) -> list[Any]:
        """Return the entries this type handles from *config*."""
        ...

    def generate_one(
        self,
        entry: Any,  # noqa: ANN401
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Generate files for a single *entry*."""
        ...

    def after_all(
        self,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Run after all entries for one app are generated.

        Only called when :meth:`entries` returned a non-empty
        list.
        """
        ...

    def after_apps(
        self,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Run once after all apps are processed.

        Use this for project-level files that depend on the
        full set of apps (e.g. a root router that mounts every
        app).
        """
        ...


# -------------------------------------------------------------------
# Registry
# -------------------------------------------------------------------

_ENTRY_TYPES: list[EntryType] = []


def register_entry_type(entry_type: EntryType) -> None:
    """Register an :class:`EntryType` for generation."""
    _ENTRY_TYPES.append(entry_type)


def get_entry_types() -> list[EntryType]:
    """Return the registered entry types (read-only copy)."""
    return list(_ENTRY_TYPES)


# -------------------------------------------------------------------
# Built-in: ResourceEntryType
# -------------------------------------------------------------------


class ResourceEntryType:
    """Entry type for config resources.

    Handles scaffold generation, per-resource file generation,
    app-level routing, and project-level routing.
    """

    def before_apps(
        self,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Emit auth and database scaffold files."""
        if config.auth or config.databases:
            return generate_scaffold(config)
        return []

    def entries(
        self,
        config: KilnConfig,
    ) -> list[ResourceConfig]:
        """Return ``config.resources``."""
        return list(config.resources)

    def generate_one(
        self,
        entry: ResourceConfig,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Generate files for a single resource."""
        return generate_resource(entry, config)

    def after_all(
        self,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Emit the app router and shared utils."""
        files: list[GeneratedFile] = []
        files.extend(generate_app_router(config))
        files.extend(generate_utils())
        return files

    def after_apps(
        self,
        config: KilnConfig,
    ) -> list[GeneratedFile]:
        """Emit the project root router when multi-app."""
        if config.apps:
            return generate_project_router(config)
        return []


register_entry_type(ResourceEntryType())


# -------------------------------------------------------------------
# Main entry point
# -------------------------------------------------------------------


def generate(config: KilnConfig) -> list[GeneratedFile]:
    """Generate all files from a kiln config.

    Args:
        config: The validated kiln configuration.

    Returns:
        Flat list of all generated files.

    """
    files: list[GeneratedFile] = []

    for entry_type in _ENTRY_TYPES:
        files.extend(entry_type.before_apps(config))

    app_configs = _resolve_apps(config)

    for app_cfg in app_configs:
        for entry_type in _ENTRY_TYPES:
            items = entry_type.entries(app_cfg)
            for item in items:
                files.extend(entry_type.generate_one(item, app_cfg))
            if items:
                files.extend(entry_type.after_all(app_cfg))

    for entry_type in _ENTRY_TYPES:
        files.extend(entry_type.after_apps(config))

    return files


def _resolve_apps(
    config: KilnConfig,
) -> list[KilnConfig]:
    """Return per-app configs with shared settings merged."""
    if config.apps:
        result = []
        for app_ref in config.apps:
            ops = (
                app_ref.config.operations
                if app_ref.config.operations is not None
                else config.operations
            )
            merged = app_ref.config.model_copy(
                update={
                    "auth": config.auth,
                    "databases": config.databases,
                    "operations": ops,
                }
            )
            result.append(merged)
        return result
    if config.resources:
        return [config]
    return []
