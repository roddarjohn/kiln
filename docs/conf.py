import tomllib
from pathlib import Path

_pyproject = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml")
    .read_text()
)

project = "kiln"
author = "Rodda John"
copyright = "2026, Rodda John"
version = _pyproject["project"]["version"]
release = version
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
]

# -- Theme -----------------------------------------------------------
html_theme = "alabaster"
html_title = "kiln"
html_theme_options = {
    "description": "CLI for autogenerating files from templates",
    "github_user": "roddajohn",
    "github_repo": "kiln",
    "github_button": False,
    "fixed_sidebar": True,
    "show_powered_by": False,
    "sidebar_collapse": True,
    "extra_nav_links": {
        "GitHub": "https://github.com/roddarjohn/kiln",
    },
    "font_family": (
        "'Source Sans Pro', 'Segoe UI', Helvetica, Arial,"
        " sans-serif"
    ),
    "code_font_family": (
        "'Source Code Pro', 'SFMono-Regular', Menlo,"
        " Consolas, monospace"
    ),
    "page_width": "940px",
    "sidebar_width": "220px",
}
html_sidebars = {
    "**": [
        "about.html",
        "searchbox.html",
        "navigation.html",
        "relations.html",
        "versioning.html",
    ],
}

# -- General ---------------------------------------------------------
exclude_patterns = ["_generated"]
myst_heading_anchors = 3
add_module_names = False
#: Render type annotations only in signatures, not in description
#: blocks.  sphinx-autodoc-typehints inlines third-party
#: docstrings (notably SQLAlchemy's) when given the chance, and
#: those docstrings carry RST that docutils can't parse cleanly
#: -- ``signature``-only avoids that whole class of noise.
autodoc_typehints = "signature"
nitpicky = True
#: Targets that aren't worth resolving (third-party libraries
#: without an intersphinx inventory, or scoped-name placeholders
#: that the Pydantic Annotated[..., Scoped(name="...")] pattern
#: surfaces to autodoc as bare class names).
nitpick_ignore = [
    # pgqueuer has no published Sphinx inventory.
    ("py:class", "pgqueuer.Queries"),
    ("py:class", "pgqueuer.Job"),
    ("py:class", "pgqueuer.PgQueuer"),
    ("py:meth", "pgqueuer.Queries.enqueue"),
    ("py:class", "pgqueuer.adapters.persistence.queries.Queries"),
    ("py:class", "AsyncpgDriver"),
    # FastAPI exposes HTTPException via runtime imports but its
    # docs site doesn't ship an objects.inv with API-level entries.
    ("py:exc", "HTTPException"),
    # Annotated[..., Scoped(name="<scope>")] surfaces the scope
    # name string to autodoc as if it were a class.
    ("py:class", "app"),
    ("py:class", "resource"),
    ("py:class", "operation"),
    ("py:class", "modifier"),
    # NamedTuple internals (private API).
    ("py:class", "foundry.operation.OperationEntry"),
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest", None),
    "sqlalchemy": ("https://docs.sqlalchemy.org/en/20", None),
    "jinja2": ("https://jinja.palletsprojects.com/en/stable", None),
    "boto3": ("https://boto3.amazonaws.com/v1/documentation/api/latest", None),
    "fastapi": ("https://fastapi.tiangolo.com", None),
}

suppress_warnings = [
    "sphinx_autodoc_typehints.forward_reference",
    # SQLAlchemy ships docstrings that pull in psycopg-only types;
    # the guarded import warns even though we use the asyncpg path.
    "sphinx_autodoc_typehints.guarded_import",
    # Pygments doesn't have a strict jsonnet lexer; relaxed mode
    # produces correct output, the warning is just noise.
    "misc.highlighting_failure",
    # SQLAlchemy ships docstrings using its own RST conventions
    # (:paramref:, deeper indentation than docutils accepts).  The
    # typehints extension inlines them while resolving cross-refs.
    # These messages have no source location because the parser
    # context is the inlined string, not a tracked file.
    "docutils",
]

templates_path = ["_templates"]


def _patch_autodoc_typehints_for_pep749():
    """Make sphinx-autodoc-typehints survive PEP 749 lazy annotations.

    Under Python 3.14's PEP 749, a class or function's
    ``__annotations__`` is no longer a plain dict -- accessing it
    triggers evaluation of the annotation expressions, which raises
    ``NameError`` for any name only imported under
    ``if TYPE_CHECKING:``.  sphinx-autodoc-typehints 3.10.x has
    several call sites that touch ``__annotations__`` directly
    (``getattr(obj, "__annotations__", None)`` and friends) without
    falling back on the ``annotationlib`` API, so any module with
    a ``TYPE_CHECKING``-guarded annotation that the lazy machinery
    can't resolve crashes the autodoc handlers and fails the
    ``-W`` build.

    Note: kiln source files still carry ``from __future__ import
    annotations`` so the common rendering path (intersphinx
    cross-references for stringified types) keeps working.  The
    future import means annotations are stored as strings and the
    lazy evaluator never runs, so this shim almost never fires.
    It exists as a soft-fail backstop -- if a contributor drops
    the future import in some file, docs will still build (losing
    a couple of cross-references in that file's signatures) rather
    than crashing the whole ``-W`` job.

    Drop this shim once upstream ships PEP 749 support.  Tracking:
    https://github.com/tox-dev/sphinx-autodoc-typehints/issues
    """
    import annotationlib
    import functools
    import inspect as _inspect

    import sphinx_autodoc_typehints as _sat

    _orig_sig = _sat.process_signature
    _orig_doc = _sat.process_docstring

    @functools.wraps(_orig_sig)
    def _sig(app, what, name, obj, options, signature, return_annotation):
        if not callable(obj):
            return None
        target = (
            getattr(obj, "__init__", getattr(obj, "__new__", None))
            if _inspect.isclass(obj)
            else obj
        )
        try:
            anns = annotationlib.get_annotations(
                target, format=annotationlib.Format.FORWARDREF
            )
        except Exception:
            anns = {}
        if not anns:
            return None
        try:
            return _orig_sig(
                app, what, name, obj, options, signature, return_annotation
            )
        except NameError:
            return None

    @functools.wraps(_orig_doc)
    def _doc(app, what, name, obj, options, lines):
        try:
            return _orig_doc(app, what, name, obj, options, lines)
        except NameError:
            return None

    _sat.process_signature = _sig
    _sat.process_docstring = _doc


_patch_autodoc_typehints_for_pep749()


def setup(app):  # noqa: D103, ANN001, ANN201
    """Register a no-op ``:paramref:`` role.

    SQLAlchemy uses ``:paramref:`` in its own docstrings.  When
    sphinx-autodoc-typehints inlines those docstrings to resolve
    cross-references, the unknown role would error out under
    ``-W``.  We register a passthrough that renders the contents
    as inline literal -- equivalent to declaring the role a
    no-op, without depending on sphinx-paramlinks.

    Note: the same docstring inlining surfaces other docutils
    parse warnings (mismatched backticks, indentation jumps in
    SQLAlchemy's RST).  Those go straight to stderr without a
    source location, so they can't be filtered via sphinx config.
    The ``just docs-check`` recipe filters them at the shell
    level instead -- only docutils system messages with no
    source path are dropped, so warnings against our own code
    always reach the build.
    """
    from docutils import nodes
    from docutils.parsers.rst import roles

    def paramref(  # noqa: PLR0913
        _name, _rawtext, text, _lineno, _inliner, options=None, content=None
    ):
        del options, content
        return [nodes.literal(text, text)], []

    roles.register_local_role("paramref", paramref)
