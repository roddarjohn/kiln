# CLAUDE.md

## What is kiln?

A CLI for autogenerating files from templates.  The CLI itself is called
`foundry`; targets ship as plugins.  This repo includes:

- `be` -- FastAPI/SQLAlchemy backend codegen.
- `be_root` -- one-shot bootstrap for a `be`-driven project.
- `fe` -- React/TypeScript frontend codegen (TS types + React Query
  hooks from an OpenAPI spec).
- `fe_root` -- one-shot bootstrap for a `fe`-driven project.

The PyPI distribution name is `kiln-generator`; the on-disk repo dir,
GitHub repo, and Sphinx site stay branded `kiln`.

## Tooling

This project uses [uv](https://docs.astral.sh/uv/) for dependency management
and virtual environments. Always use `uv run` to execute commands (not raw
`python` or `pip`). Install all dependency groups with `uv sync --all-groups`.
Add dependencies via `uv add`, not `pip install`.

## Commands

```bash
just lint          # ruff check + format check
just type-check    # zuban check src/
just dev-test      # pytest (fast, local dev)
just test          # tox (full isolation, matches CI)
just docs          # build Sphinx HTML docs
just setup         # install pre-commit hooks
```

Auto-fix: `uv run --group lint ruff check --fix && uv run --group lint ruff format`

## Code standards

### Guiding principles

- **Explicit over implicit.** No magic. If behavior is not obvious from
  reading the code, it needs a docstring or comment explaining *why*.
- **Simple over clever.** Prefer the straightforward approach. Three similar
  lines are better than a premature abstraction. Do not design for
  hypothetical future requirements.
- **Readability counts.** Code is read far more than it is written. Optimize
  for the next person reading, not the person writing.
- **Errors should never pass silently.** Catch specific exceptions. Never use
  bare `except:`. Validate at system boundaries (user input, external APIs).
  Trust internal code.

### Style (enforced by ruff + zuban)

- **Line length:** 80 characters.
- **Type annotations:** Required on all public API. Avoid `Any` unless
  absolutely necessary. Run `just type-check` to verify.
- **Docstrings:** Google style. Required on all public classes, functions, and
  methods. Describe *what* and *why*, not *how*.
- **Imports:** Top-level only. No local/deferred imports unless there is a
  genuine circular-import reason. Ruff handles ordering.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes,
  `UPPER_CASE` for constants. Use descriptive names -- `requires_python` not
  `rp`.

### Testing

- All changes MUST be tested. If a behavior changed, a test should cover it.
- Run `just dev-test` for fast feedback, `just test` before submitting.
- Run `just coverage` to get per-file coverage figures via coverage.py.

**Using coverage to avoid regressions:** Before starting a change, run
`just coverage` and note the coverage for the files you are about to
modify. After making your change, run it again. If coverage on those
files has dropped, you have likely introduced untested code paths and
should add tests before submitting. Coverage is a signal, not a target
-- a line being covered does not mean it is correctly tested, but an
uncovered line is a clear gap.

Tests live in:
- `tests/unit/` -- pure Python tests
- `tests/integration/` -- end-to-end CLI tests

### Contribution checklist

Before submitting, all of these must pass (CI will enforce):

1. `just lint` -- no lint or format violations
2. `just type-check` -- no type errors
3. `just test` -- full test suite green
4. No hardcoded credentials, secrets, or debug statements

## Hooks

A post-tool-use hook runs `just lint && just type-check` after every file
edit. If the hook fails, fix the issue before continuing.
