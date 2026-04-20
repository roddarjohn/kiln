"""Tests for kiln_core primitives."""

import pytest

from kiln_core import (
    FileSpec,
    GeneratedFile,
    ImportCollector,
    Name,
    create_jinja_env,
    prefix_import,
    render_snippet,
    split_dotted_class,
    wire_exports,
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
        f.path = "bar.py"  # type: ignore[misc]


# -------------------------------------------------------------------
# ImportCollector
# -------------------------------------------------------------------


def test_import_collector_bare():
    c = ImportCollector()
    c.add("uuid")
    assert c.lines() == ["import uuid"]


def test_import_collector_from():
    c = ImportCollector()
    c.add_from("datetime", "datetime", "date")
    assert c.lines() == ["from datetime import date, datetime"]


def test_import_collector_merges():
    c = ImportCollector()
    c.add_from("datetime", "datetime")
    c.add_from("datetime", "date")
    assert c.lines() == ["from datetime import date, datetime"]


def test_import_collector_deduplicates():
    c = ImportCollector()
    c.add("uuid")
    c.add("uuid")
    c.add_from("datetime", "date")
    c.add_from("datetime", "date")
    assert c.lines() == [
        "import uuid",
        "from datetime import date",
    ]


def test_import_collector_block_empty():
    c = ImportCollector()
    assert c.block() == ""


def test_import_collector_block_nonempty():
    c = ImportCollector()
    c.add("uuid")
    result = c.block()
    assert result == "import uuid\n"


def test_import_collector_groups():
    c = ImportCollector()
    c.add_from("__future__", "annotations")
    c.add("uuid")
    c.add_from("pydantic", "BaseModel")
    lines = c.lines()
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
# FileSpec
# -------------------------------------------------------------------


def test_filespec_module_with_prefix():
    spec = FileSpec(
        path="myapp/schemas/user.py",
        template="t.j2",
        imports=ImportCollector(),
        package_prefix="_generated",
    )
    assert spec.module == "_generated.myapp.schemas.user"


def test_filespec_module_without_prefix():
    spec = FileSpec(
        path="myapp/schemas/user.py",
        template="t.j2",
        imports=ImportCollector(),
        package_prefix="",
    )
    assert spec.module == "myapp.schemas.user"


def test_filespec_render(tmp_path):
    tmpl_dir = tmp_path / "templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "simple.py.j2").write_text("{{ import_block }}\nx = 1\n")
    env = create_jinja_env(tmpl_dir)
    spec = FileSpec(
        path="out.py",
        template="simple.py.j2",
        imports=ImportCollector(),
    )
    spec.imports.add("os")
    result = spec.render(env)
    assert isinstance(result, GeneratedFile)
    assert result.path == "out.py"
    assert "import os" in result.content
    assert "x = 1" in result.content


def test_filespec_exports_default_empty():
    spec = FileSpec(
        path="x.py",
        template="t.j2",
        imports=ImportCollector(),
    )
    assert spec.exports == []


# -------------------------------------------------------------------
# create_jinja_env + render_snippet
# -------------------------------------------------------------------


def test_create_jinja_env(tmp_path):
    tmpl_dir = tmp_path / "templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "hello.j2").write_text("Hello {{ name }}!")
    env = create_jinja_env(tmpl_dir)
    result = env.get_template("hello.j2").render(name="world")
    assert result == "Hello world!"


def test_render_snippet(tmp_path):
    tmpl_dir = tmp_path / "templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "greet.j2").write_text("  Hi {{ who }}  ")
    env = create_jinja_env(tmpl_dir)
    result = render_snippet(env, "greet.j2", who="there")
    assert result == "Hi there"


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


def test_write_files_clean(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.py").write_text("old")
    files = [GeneratedFile("fresh.py", "new")]
    write_files(files, out, clean=True)
    assert (out / "fresh.py").exists()
    assert not (out / "stale.py").exists()


# -------------------------------------------------------------------
# wire_exports
# -------------------------------------------------------------------


def test_wire_exports_imports_referenced_name():
    schema = FileSpec(
        path="app/schemas/user.py",
        template="t.j2",
        imports=ImportCollector(),
        exports=["UserResource", "UserCreateRequest"],
    )
    route = FileSpec(
        path="app/routes/user.py",
        template="t.j2",
        imports=ImportCollector(),
        context={"handlers": "body: UserCreateRequest"},
    )
    wire_exports({"schema": schema, "route": route})
    lines = route.imports.lines()
    assert any("UserCreateRequest" in ln for ln in lines)
    assert not any("UserResource" in ln for ln in lines)


def test_wire_exports_skips_unreferenced():
    schema = FileSpec(
        path="app/schemas/user.py",
        template="t.j2",
        imports=ImportCollector(),
        exports=["UserResource"],
    )
    route = FileSpec(
        path="app/routes/user.py",
        template="t.j2",
        imports=ImportCollector(),
        context={"handlers": "no references here"},
    )
    wire_exports({"schema": schema, "route": route})
    assert route.imports.lines() == []


def test_wire_exports_respects_insertion_order():
    first = FileSpec(
        path="a.py",
        template="t.j2",
        imports=ImportCollector(),
        exports=["Foo"],
        context={"text": "Bar"},
    )
    second = FileSpec(
        path="b.py",
        template="t.j2",
        imports=ImportCollector(),
        exports=["Bar"],
        context={"text": "Foo"},
    )
    wire_exports({"first": first, "second": second})
    # second references Foo (from first) → imported
    assert any("Foo" in ln for ln in second.imports.lines())
    # first references Bar (from second) but second comes
    # after first so it is NOT wired
    assert first.imports.lines() == []


def test_wire_exports_nested_context():
    schema = FileSpec(
        path="schemas.py",
        template="t.j2",
        imports=ImportCollector(),
        exports=["MyModel"],
    )
    route = FileSpec(
        path="routes.py",
        template="t.j2",
        imports=ImportCollector(),
        context={"nested": [{"deep": "uses MyModel here"}]},
    )
    wire_exports({"schema": schema, "route": route})
    assert any("MyModel" in ln for ln in route.imports.lines())
