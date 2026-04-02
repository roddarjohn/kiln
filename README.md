# kiln

[![PyPI](https://img.shields.io/pypi/v/kiln)](https://pypi.org/project/kiln/)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://roddarjohn.github.io/kiln/)

> **Pre-alpha** — kiln is under active development. APIs may change
> between releases. Feedback and contributions are welcome.

**CLI for autogenerating files from templates.**

---

## Install

```bash
pip install kiln            # or: uv add kiln
```

## Quick start

```bash
kiln --help
```

## Documentation

Full documentation is available at
[roddarjohn.github.io/kiln](https://roddarjohn.github.io/kiln/).

- [Usage](https://roddarjohn.github.io/kiln/usage.html)
- [API reference](https://roddarjohn.github.io/kiln/api.html)
- [Development guide](https://roddarjohn.github.io/kiln/development.html)

## Development

**Prerequisites:** [uv](https://docs.astral.sh/uv/), [just](https://just.systems), and `jsonnetfmt` — `brew install go-jsonnet` on macOS, `sudo apt install jsonnet` on Debian/Ubuntu.

```bash
# Clone and install
git clone https://github.com/<username>/kiln.git
cd kiln
uv sync --all-groups

# Install pre-commit hooks (ruff + jsonnetfmt run on every commit)
just setup

# Run checks
just lint          # ruff check + format
just type-check    # ty check src/
just dev-test      # pytest (fast, local dev)
just test          # tox (full isolation)
just docs          # build Sphinx HTML docs
```

## License

See [LICENSE](LICENSE) for details.
