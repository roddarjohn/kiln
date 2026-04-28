"""be_root -- foundry target that scaffolds a fresh be project.

Where the ``be`` target turns a be config into FastAPI route
modules under ``_generated/``, ``be_root`` runs once at project
creation time to lay down the surrounding files: ``main.py``,
``pyproject.toml``, ``justfile``, the starter ``project.jsonnet``
config be then consumes, and a few small dotfiles.

Use it like::

    foundry generate --target be_root --config root.jsonnet --out .

The config schema (:class:`be_root.config.RootConfig`) is
deliberately thin -- a project name, a default app module, and an
optional description.  Everything else is hard-coded in the
templates because the whole point is to give users a generic,
working starting point they can edit as soon as the files land.
"""
