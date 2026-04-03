"""Generate src/kiln/stdlib/pgcraft/plugins.libsonnet from pgcraft.

Walks pgcraft.plugins.* and pgcraft.extensions.* to discover Plugin
subclasses, inspects their __init__ signatures, and emits a Jsonnet
stdlib file with typed helper functions matching the real pgcraft API.

Run with::

    just generate-pgcraft-stdlib

Re-run whenever pgcraft is updated to pick up new plugins.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "kiln" / "stdlib" / "pgcraft" / "plugins.libsonnet"

# Params that are pgcraft-internal context keys or require Callable values.
# These are never exposed in the Jsonnet stdlib.
_SKIP_PARAMS: frozenset[str] = frozenset(
    {
        "self",
        # pgcraft factory context keys
        "table_key",
        "table_keys",
        "view_key",
        # Callable / non-serializable
        "query",
        "query_builder",
        "proxy_builder",
        "ops_builder",
        "naming_defaults",
        "function_key",
        "trigger_key",
        # ViewPlugin internal (not FieldConfig.primary_key)
        "primary_key",
        # Advanced / rarely needed
        "extra_requires",
        "permitted_operations",
    }
)

# Friendly short names keyed by fully-qualified class path.
# Anything not listed here is skipped.
# Tuple value → PK plugin under pk.* namespace (string alias, no args).
# String value → regular plugin alias.
_PLUGIN_ALIASES: dict[str, str | tuple[str, str]] = {
    # PK plugins — column_name is set from the field name by the generator.
    "pgcraft.plugins.pk.UUIDV4PKPlugin": ("pk", "uuid_v4"),
    "pgcraft.plugins.pk.UUIDV7PKPlugin": ("pk", "uuid_v7"),
    "pgcraft.plugins.pk.SerialPKPlugin": ("pk", "serial"),
    # No-arg plugins
    "pgcraft.plugins.check.TableCheckPlugin": "check",
    "pgcraft.plugins.fk.TableFKPlugin": "fk",
    "pgcraft.plugins.index.TableIndexPlugin": "index",
    "pgcraft.plugins.ledger.DoubleEntryTriggerPlugin": "double_entry_trigger",
    # Configurable plugins
    "pgcraft.extensions.postgrest.plugin.PostgRESTPlugin": "postgrest",
    "pgcraft.plugins.created_at.CreatedAtPlugin": "created_at",
    "pgcraft.plugins.ledger.DoubleEntryPlugin": "double_entry",
    "pgcraft.plugins.ledger.LedgerBalanceCheckPlugin": "ledger_balance_check",
}


def _discover_plugins() -> dict[str, type]:
    """Walk pgcraft packages; return {dotted_path: class} for plugins."""
    import pgcraft.extensions
    import pgcraft.plugins
    from pgcraft.plugin import Plugin

    found: dict[str, type] = {}
    for pkg in (pgcraft.plugins, pgcraft.extensions):
        for _, modname, _ in pkgutil.walk_packages(
            path=pkg.__path__,
            prefix=pkg.__name__ + ".",
            onerror=lambda _: None,
        ):
            try:
                mod = importlib.import_module(modname)
            except Exception:  # noqa: BLE001
                continue
            for name, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    obj.__module__ == modname
                    and issubclass(obj, Plugin)
                    and not name.startswith("_")
                ):
                    found[f"{modname}.{name}"] = obj
    return found


def _configurable_params(cls: type) -> list[tuple[str, Any]]:
    """Return ``[(name, default)]`` for configurable ``__init__`` params.

    ``inspect.Parameter.empty`` signals a required parameter.
    """
    sig = inspect.signature(cls.__init__)
    result = []
    for name, param in sig.parameters.items():
        if name in _SKIP_PARAMS:
            continue
        if "Callable" in str(param.annotation):
            continue
        result.append((name, param.default))
    return result


def _to_jsonnet(value: Any) -> str:
    """Convert a Python default value to its Jsonnet literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        parts = ", ".join(
            f'"{x}"' if isinstance(x, str) else str(x) for x in value
        )
        return f"[{parts}]"
    return repr(value)


def _render_plugin_fn(fq_path: str, cls: type, alias: str) -> str:
    """Render a Jsonnet function for a configurable plugin."""
    params = _configurable_params(cls)
    required = [(n, d) for n, d in params if d is inspect.Parameter.empty]
    with_default = [
        (n, d)
        for n, d in params
        if d is not inspect.Parameter.empty and d is not None
    ]
    nullable = [
        (n, d)
        for n, d in params
        if d is not inspect.Parameter.empty and d is None
    ]

    doc = cls.__doc__.splitlines()[0].strip() if cls.__doc__ else alias
    lines = [
        f"  // {doc}",
        f"  {alias}(opts={{}}):: {{",
        f'    path: "{fq_path}",',
        "    args: {",
    ]
    for name, _ in required:
        lines.append(f"      {name}: opts.{name},")
    for name, default in with_default:
        jd = _to_jsonnet(default)
        lines.append(f'      {name}: std.get(opts, "{name}", {jd}),')
    lines.append("    }")
    for name, _ in nullable:
        lines.append(
            f'    + (if "{name}" in opts'
            f" then {{ {name}: opts.{name} }} else {{}})"
        )
    lines.append("  },")
    return "\n".join(lines)


def _render_noarg_plugin(fq_path: str, cls: type, alias: str) -> str:
    """Render a string constant for a no-arg plugin."""
    doc = cls.__doc__.splitlines()[0].strip() if cls.__doc__ else alias
    return f'  // {doc}\n  {alias}: "{fq_path}",'


def _render_pk_plugin(fq_path: str, alias: str) -> str:
    """Render a PK plugin string constant."""
    return f'    {alias}: "{fq_path}",'


_SEP = "  // " + "-" * 69


def generate() -> str:
    """Generate the full plugins.libsonnet content."""
    plugins = _discover_plugins()

    pk_lines: list[str] = []
    noarg_lines: list[str] = []
    configurable_lines: list[str] = []

    for fq_path, alias_info in _PLUGIN_ALIASES.items():
        cls = plugins.get(fq_path)
        if cls is None:
            continue
        if isinstance(alias_info, tuple):
            _, short = alias_info
            pk_lines.append(_render_pk_plugin(fq_path, short))
        else:
            alias = alias_info
            params = _configurable_params(cls)
            if params:
                configurable_lines.append(
                    _render_plugin_fn(fq_path, cls, alias)
                )
            else:
                noarg_lines.append(_render_noarg_plugin(fq_path, cls, alias))

    sections: list[str] = [
        "// kiln stdlib — pgcraft plugin helpers",
        "//",
        "// Auto-generated from pgcraft introspection — do not edit by hand.",
        "// Re-generate with: just generate-pgcraft-stdlib",
        "//",
        "// Usage:",
        "//   local plugins = import 'kiln/pgcraft/plugins.libsonnet';",
        "//",
        "//   // PK plugin (use as primary_key value on a field):",
        "//   field.uuid('id', primary_key=plugins.pk.uuid_v4)",
        "//   field.uuid('id', primary_key=plugins.pk.uuid_v7)",
        "//",
        "//   // No-arg plugin (use in pgcraft_plugins list):",
        "//   pgcraft_plugins: [plugins.check]",
        "//",
        "//   // Configurable plugin (use in pgcraft_plugins list):",
        "//   pgcraft_plugins: [plugins.postgrest({ grants: ['select'] })]",
        "{",
        _SEP,
        "  // PK plugins — use as primary_key on uuid/int fields.",
        "  // column_name is derived from the field name automatically.",
        _SEP,
        "  pk: {",
        *pk_lines,
        "  },",
        "",
        _SEP,
        "  // No-arg plugins — pass as strings in pgcraft_plugins.",
        _SEP,
        *noarg_lines,
        "",
        _SEP,
        "  // Configurable plugins — call as functions in pgcraft_plugins.",
        _SEP,
        *configurable_lines,
        "}",
    ]
    return "\n".join(sections) + "\n"


def main() -> None:
    """Write the generated libsonnet to disk."""
    content = generate()
    OUT.write_text(content)
    print(f"Written {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
