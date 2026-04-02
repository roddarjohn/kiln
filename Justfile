# Install git hooks (run once after cloning)
setup:
    uvx pre-commit install

# Run ruff linter and formatter check
lint:
    uv run --group lint ruff check && uv run --group lint ruff format --check

# Run ty type checker
type-check:
    uv run --group lint ty check src/

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

# Build HTML docs for all versions (output in docs/_build/html)
docs:
    uv run python scripts/docs/build_versioned_docs.py

# Serve docs with live reload for editing (http://127.0.0.1:8000)
serve-docs-autoreload:
    uv run --group docs sphinx-autobuild docs docs/_build/html

# Serve the full versioned docs build (http://localhost:8000)
serve-docs-static: docs
    python -m http.server -d docs/_build/html 8000
