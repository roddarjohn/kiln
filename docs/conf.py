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
nitpicky = True
nitpick_ignore = []

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

suppress_warnings = ["sphinx_autodoc_typehints.forward_reference"]

templates_path = ["_templates"]
