# Install git hooks (run once after cloning)
setup:
    uvx pre-commit install

# Run ruff linter and formatter check, plus the blank-line checker
lint:
    uv run --group lint ruff check && uv run --group lint ruff format --check && uv run python scripts/check_control_blank_lines.py

# Run zuban type checker (native mode, not mypy-compatible)
type-check:
    uv run --group lint zuban check src/

# Run tests via tox (full isolation, builds package as sdist)
test *args:
    uv run tox -- {{args}}

# Run tests directly via uv (faster, for local development)
dev-test *args:
    uv run pytest {{args}}

# Run tests with coverage report (branch + line coverage)
coverage *args:
    uv run coverage run -m pytest {{args}}
    uv run coverage report

# Run coverage and write XML output (used by CI)
coverage-ci *args:
    uv run coverage run -m pytest {{args}}
    uv run coverage xml -o coverage.xml

# Re-generate src/kiln/jsonnet/pgcraft/plugins.libsonnet from pgcraft introspection
generate-pgcraft-stdlib:
    uv run --group playground python scripts/generate_pgcraft_stdlib.py

# Build HTML docs for all versions (output in docs/_build/html)
docs:
    uv run python scripts/docs/build_versioned_docs.py

# Strict single-version docs build (CI uses this to fail PRs that
# introduce sphinx warnings).  No version-shim, no caching -- just
# the current tree under -W so any warning trips a non-zero exit.
#
# A small docutils-level filter excludes RST-parse messages with no
# source location: those come from sphinx-autodoc-typehints inlining
# foreign-library docstrings (notably SQLAlchemy's, which use
# library-internal RST conventions docutils flags).  Real warnings
# against our source always carry a location, so the filter never
# masks anything from our own code.
docs-check:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run --group docs sphinx-build -W --keep-going -b html \
        docs docs/_build/check 2> >(
            grep -v -E '^:[0-9]+: \(WARNING/2\) (Inline (literal|interpreted text)|Block quote ends)' \
            | grep -v -E '^:[0-9]+: \(ERROR/3\) Unexpected indentation' >&2
        )

# Serve docs with live reload for editing (http://127.0.0.1:8000)
serve-docs-autoreload:
    uv run --group docs sphinx-autobuild docs docs/_build/html

# Serve the full versioned docs build (http://localhost:8000)
serve-docs-static: docs
    python -m http.server -d docs/_build/html 8000
