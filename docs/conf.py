import inspect
import re
import textwrap
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
    # pgcraft has no published Sphinx inventory.
    ("py:class", "pgcraft.plugins.pk.UUIDV4PKPlugin"),
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

#: Match a top-level ``if TYPE_CHECKING:`` block and capture its
#: indented body.  The lookahead ``(?=\n\S)`` stops at the first
#: non-indented following line, mirroring sphinx-autodoc-typehints'
#: own guarded-import scanner.
_TYPE_GUARD_RE = re.compile(
    r"\nif (?:typing\.)?TYPE_CHECKING:[^\n]*([\s\S]*?)(?=\n\S)"
)
_GUARD_RESOLVED: set[str] = set()


def _preresolve_type_guards(  # noqa: PLR0913
    _app, _what, _name, obj, _options, _signature, _return_annotation
):
    """Exec ``if TYPE_CHECKING:`` blocks into the obj's module globals.

    sphinx-autodoc-typehints' ``process_signature`` calls
    ``inspect.signature`` (line 73 of its ``__init__``), which
    evaluates annotations eagerly and NameErrors on
    ``TYPE_CHECKING``-guarded names.  PEP 749 doesn't rescue us:
    ``inspect.signature`` doesn't use the ``Format.FORWARDREF``
    path.

    We connect with priority 100 so this runs before the lib's
    default-priority (500) handler -- by the time it walks
    annotations the guarded names are real attributes on the
    module.

    The lib has its own ``_resolver._resolve_type_guarded_imports``
    that does the same thing, but it only runs from
    ``process_docstring``, which fires *after* ``process_signature``.
    """
    module = inspect.getmodule(obj)

    if module is None or module.__name__ in _GUARD_RESOLVED:
        return

    _GUARD_RESOLVED.add(module.__name__)

    try:
        src = inspect.getsource(module)

    except (TypeError, OSError):
        return

    for body in _TYPE_GUARD_RE.findall(src):
        try:
            exec(textwrap.dedent(body), module.__dict__)  # noqa: S102

        except Exception:  # noqa: BLE001, S110
            pass


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
    app.connect("autodoc-process-signature", _preresolve_type_guards, priority=100)
