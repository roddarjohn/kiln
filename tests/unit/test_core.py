"""Tests for foundry primitives."""

import pytest

from foundry import (
    GeneratedFile,
    ImportCollector,
    Name,
    create_jinja_env,
    prefix_import,
    render_template,
    split_dotted_class,
    write_files,
)

# -------------------------------------------------------------------
# GeneratedFile
# -------------------------------------------------------------------


def test_generated_file_is_frozen():
    f = GeneratedFile(path="foo.py", content="# hi")
    assert f.path == "foo.py"
    assert f.content == "# hi"

    with pytest.raises(AttributeError):
        f.path = "bar.py"


# -------------------------------------------------------------------
# ImportCollector
# -------------------------------------------------------------------


def test_import_collector_bare():
    c = ImportCollector()
    c.add("uuid")
    assert c.format("python").rstrip("\n").splitlines() == ["import uuid"]


def test_import_collector_from():
    c = ImportCollector()
    c.add_from("datetime", "datetime", "date")
    assert c.format("python").rstrip("\n").splitlines() == [
        "from datetime import date, datetime"
    ]


def test_import_collector_merges():
    c = ImportCollector()
    c.add_from("datetime", "datetime")
    c.add_from("datetime", "date")
    assert c.format("python").rstrip("\n").splitlines() == [
        "from datetime import date, datetime"
    ]


def test_import_collector_deduplicates():
    c = ImportCollector()
    c.add("uuid")
    c.add("uuid")
    c.add_from("datetime", "date")
    c.add_from("datetime", "date")
    assert c.format("python").rstrip("\n").splitlines() == [
        "import uuid",
        "from datetime import date",
    ]


def test_import_collector_block_empty():
    c = ImportCollector()
    assert c.format("python") == ""


def test_import_collector_block_nonempty():
    c = ImportCollector()
    c.add("uuid")
    result = c.format("python")
    assert result == "import uuid\n"


def test_import_collector_groups():
    c = ImportCollector()
    c.add_from("__future__", "annotations")
    c.add("uuid")
    c.add_from("pydantic", "BaseModel")
    lines = c.format("python").rstrip("\n").splitlines()
    assert lines[0] == "from __future__ import annotations"
    assert "" in lines  # blank separator
    assert "import uuid" in lines
    assert "from pydantic import BaseModel" in lines


# -------------------------------------------------------------------
# Name
# -------------------------------------------------------------------


def test_name_pascal_from_snake():
    assert Name("publish_article").pascal == "PublishArticle"


def test_name_pascal_already_pascal():
    assert Name("Article").pascal == "Article"


def test_name_pascal_preserves_multi_word():
    assert Name("StockMovement").pascal == "StockMovement"


def test_name_lower():
    assert Name("Article").lower == "article"


def test_name_slug():
    assert Name("publish_article").slug == "publish-article"


def test_name_suffixed():
    assert Name("Article").suffixed("Resource") == "ArticleResource"


def test_name_from_dotted():
    module, name = Name.from_dotted("myapp.models.Article")
    assert module == "myapp.models"
    assert name.pascal == "Article"


# -------------------------------------------------------------------
# split_dotted_class
# -------------------------------------------------------------------


def test_split_dotted_class():
    assert split_dotted_class("a.B") == ("a", "B")


def test_split_dotted_class_invalid():
    with pytest.raises(ValueError, match="not a valid"):
        split_dotted_class("NoDot")


# ---------------------------------------------------------------------------
# prefix_import helpers
# ---------------------------------------------------------------------------


def test_prefix_import_with_prefix():
    assert prefix_import("_gen", "app", "routes") == "_gen.app.routes"


def test_prefix_import_empty_prefix():
    assert prefix_import("", "app", "routes") == "app.routes"


# -------------------------------------------------------------------
# create_jinja_env + render_template
# -------------------------------------------------------------------


def test_create_jinja_env(tmp_path):
    tmpl_dir = tmp_path / "templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "hello.j2").write_text("Hello {{ name }}!")
    env = create_jinja_env(tmpl_dir)
    result = env.get_template("hello.j2").render(name="world")
    assert result == "Hello world!"


def test_render_template(tmp_path):
    tmpl_dir = tmp_path / "templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "greet.j2").write_text("  Hi {{ who }}  ")
    env = create_jinja_env(tmpl_dir)
    # Raw render; callers apply their own trim policy.
    result = render_template(env, "greet.j2", who="there")
    assert result == "  Hi there  "


# -------------------------------------------------------------------
# write_files
# -------------------------------------------------------------------


def test_write_files_creates_files(tmp_path):
    files = [
        GeneratedFile("a.py", "# a"),
        GeneratedFile("sub/b.py", "# b"),
    ]
    written = write_files(files, tmp_path)
    assert written == 2
    assert (tmp_path / "a.py").read_text() == "# a"
    assert (tmp_path / "sub" / "b.py").read_text() == "# b"


def test_write_files_overwrites(tmp_path):
    (tmp_path / "a.py").write_text("old")
    files = [GeneratedFile("a.py", "new")]
    write_files(files, tmp_path)
    assert (tmp_path / "a.py").read_text() == "new"


# -------------------------------------------------------------------
# write_files -- if_exists / --force / --force-paths semantics
# -------------------------------------------------------------------


def test_write_files_skip_leaves_existing_file_alone(tmp_path):
    # The "skip" policy is what makes ``just bootstrap`` safe to
    # re-run.  Hand-edited content on disk must survive.
    (tmp_path / "pyproject.toml").write_text("user-edited")
    files = [
        GeneratedFile(
            path="pyproject.toml",
            content="from-template",
            if_exists="skip",
        ),
    ]

    written = write_files(files, tmp_path)

    assert written == 0
    assert (tmp_path / "pyproject.toml").read_text() == "user-edited"


def test_write_files_skip_creates_when_missing(tmp_path):
    # Skip means "skip if exists" -- a missing file must still be
    # created so the bootstrap actually scaffolds on first run.
    files = [
        GeneratedFile(
            path="pyproject.toml",
            content="fresh",
            if_exists="skip",
        ),
    ]

    written = write_files(files, tmp_path)

    assert written == 1
    assert (tmp_path / "pyproject.toml").read_text() == "fresh"


def test_write_files_force_overrides_skip(tmp_path):
    (tmp_path / "pyproject.toml").write_text("user-edited")
    files = [
        GeneratedFile(
            path="pyproject.toml",
            content="from-template",
            if_exists="skip",
        ),
    ]

    written = write_files(files, tmp_path, force=True)

    assert written == 1
    assert (tmp_path / "pyproject.toml").read_text() == "from-template"


def test_write_files_force_paths_targets_only_listed(tmp_path):
    # The whole point of --force-paths is surgical: clobber one
    # file, leave the rest alone.
    (tmp_path / "pyproject.toml").write_text("user-edited")
    (tmp_path / "main.py").write_text("user-edited")
    files = [
        GeneratedFile(
            path="pyproject.toml",
            content="from-template",
            if_exists="skip",
        ),
        GeneratedFile(
            path="main.py",
            content="from-template",
            if_exists="skip",
        ),
    ]

    written = write_files(files, tmp_path, force_paths=["pyproject.toml"])

    assert written == 1
    assert (tmp_path / "pyproject.toml").read_text() == "from-template"
    assert (tmp_path / "main.py").read_text() == "user-edited"


def test_write_files_force_paths_set_form(tmp_path):
    # Accept any iterable of paths -- CLI passes a set, callers
    # may pass a list.
    (tmp_path / "a").write_text("old")
    files = [GeneratedFile("a", "new", if_exists="skip")]

    write_files(files, tmp_path, force_paths={"a"})

    assert (tmp_path / "a").read_text() == "new"


def test_write_files_force_paths_ignored_when_force_set(tmp_path):
    # ``--force`` always wins; ``--force-paths`` is a finer-
    # grained override of ``"skip"`` and isn't needed when
    # everything is being overwritten anyway.
    (tmp_path / "a").write_text("old-a")
    (tmp_path / "b").write_text("old-b")
    files = [
        GeneratedFile("a", "new-a", if_exists="skip"),
        GeneratedFile("b", "new-b", if_exists="skip"),
    ]

    written = write_files(files, tmp_path, force=True, force_paths=["a"])

    assert written == 2
    assert (tmp_path / "a").read_text() == "new-a"
    assert (tmp_path / "b").read_text() == "new-b"


def test_write_files_overwrite_files_unaffected_by_force_flags(tmp_path):
    # The default ``"overwrite"`` policy is unrelated to the
    # force machinery; clobbering existing content is its job.
    (tmp_path / "routes.py").write_text("old")
    files = [GeneratedFile("routes.py", "new", if_exists="overwrite")]

    write_files(files, tmp_path)

    assert (tmp_path / "routes.py").read_text() == "new"
