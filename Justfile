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
