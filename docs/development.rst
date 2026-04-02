Development
===========

This guide explains how to set up a local development environment for **kiln**,
run tests, lint code, and build the documentation.

Prerequisites
-------------

You will need the following tools installed:

* `Python 3.12+ <https://www.python.org/downloads/>`_
* `uv <https://docs.astral.sh/uv/>`_ — dependency management and virtual environments
* `just <https://just.systems>`_ — command runner

Install ``uv`` by following the
`uv installation instructions <https://docs.astral.sh/uv/getting-started/installation/>`_.

Install ``just`` by following the
`just installation instructions <https://just.systems/man/en/packages.html>`_.

Fork and clone
--------------

`Fork the repository <https://github.com/roddarjohn/kiln/fork>`_ on GitHub, then clone your fork::

    git clone https://github.com/<your-username>/kiln
    cd kiln

Install all dependency groups and activate the virtual environment::

    uv sync --all-groups

Install the pre-commit hooks (runs ruff automatically on every commit)::

    just setup

That's it. You're ready to develop.

Running tests
-------------

kiln uses `pytest <https://docs.pytest.org>`_ for testing.

Tests are organised into two directories:

``tests/unit/``
    Pure Python tests.

``tests/integration/``
    End-to-end CLI tests.

For fast feedback during development, run pytest directly::

    just dev-test

To run the full test suite with tox (installs the package into a clean environment,
matching what CI does)::

    just test

Both commands pass arguments through to pytest::

    just dev-test tests/unit
    just dev-test -k test_something
    just test tests/unit

Coverage
--------

kiln uses `coverage.py <https://coverage.readthedocs.io/>`_ for
coverage reporting::

    just coverage

This runs the full pytest suite under coverage and prints a per-file
coverage table to the terminal.

Linting and formatting
-----------------------

kiln uses `ruff <https://docs.astral.sh/ruff/>`_ for linting and formatting.
It runs automatically as a pre-commit hook, but you can also run it manually::

    just lint

Ruff will check for style issues and verify formatting. To auto-fix and auto-format::

    uv run --group lint ruff check --fix
    uv run --group lint ruff format

Type checking
-------------

kiln uses `ty <https://github.com/astral-sh/ty>`_ for type checking::

    just type-check

Documentation
-------------

The docs are built with `Sphinx <https://www.sphinx-doc.org>`_ using the
alabaster theme.

To build the docs::

    just docs

To serve the docs locally with live reload at ``http://localhost:8000``::

    just serve-docs-autoreload

Contributing
------------

Contributions are welcome. Fork the repository, make your changes with tests
where applicable, verify the test suite and linter pass (see the sections
above), then open a pull request against ``main``.

Type annotations are required on all public API.

For security vulnerabilities, see
`SECURITY.md <https://github.com/roddarjohn/kiln/blob/main/SECURITY.md>`_
rather than opening a public issue.
