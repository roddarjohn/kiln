"""Naming conventions and import-path helpers for code generation.

Provides :class:`Name` for deriving conventional identifiers
(PascalCase, snake_case, slugs) from a base string, plus helpers
for splitting and constructing Python dotted import paths.
"""

from __future__ import annotations


class Name:
    """Derives conventional identifiers from a base string.

    Accepts either a ``PascalCase`` class name (e.g. ``"Article"``)
    or a ``snake_case`` identifier (e.g. ``"publish_article"``) and
    exposes the common derived forms used by code generators.

    Examples::

        model = Name("Article")
        model.pascal              # "Article"
        model.lower               # "article"
        model.suffixed("Resource")  # "ArticleResource"

        action = Name("publish_article")
        action.pascal             # "PublishArticle"
        action.slug               # "publish-article"
        action.suffixed("Request")  # "PublishArticleRequest"

    """

    def __init__(self, raw: str) -> None:  # noqa: D107
        self.raw = raw

    @property
    def pascal(self) -> str:
        """PascalCase form of the name.

        If the raw string contains no underscores and already
        starts with an uppercase letter it is returned as-is
        (assumed to already be PascalCase, e.g. ``"StockMovement"``
        from a dotted import path).
        """
        if "_" not in self.raw and self.raw[:1].isupper():
            return self.raw

        return "".join(part.capitalize() for part in self.raw.split("_"))

    @property
    def lower(self) -> str:
        """Fully lowercased form (for file/module names)."""
        return self.raw.lower()

    @property
    def slug(self) -> str:
        """Hyphenated slug form (for URL segments)."""
        return self.raw.replace("_", "-")

    def suffixed(self, suffix: str) -> str:
        """PascalCase name with *suffix* appended.

        Args:
            suffix: Class-name suffix, e.g. ``"CreateRequest"``.

        Returns:
            Combined string, e.g. ``"ArticleCreateRequest"``.

        """
        return f"{self.pascal}{suffix}"

    @classmethod
    def from_dotted(cls, dotted_path: str) -> tuple[str, Name]:
        """Create a :class:`Name` from a dotted import path.

        Args:
            dotted_path: A fully-qualified class path such as
                ``"myapp.models.Article"``.

        Returns:
            A ``(module, Name)`` tuple, e.g.
            ``("myapp.models", Name("Article"))``.

        """
        module, class_name = split_dotted_class(dotted_path)
        return module, cls(class_name)


def split_dotted_class(dotted_path: str) -> tuple[str, str]:
    """Split a dotted import path into ``(module, class_name)``.

    Args:
        dotted_path: A fully-qualified class path such as
            ``"myapp.models.Article"``.

    Returns:
        A ``(module, class_name)`` tuple, e.g.
        ``("myapp.models", "Article")``.

    Raises:
        ValueError: If *dotted_path* contains fewer than two parts.

    """
    if "." not in dotted_path:
        msg = (
            f"'{dotted_path}' is not a valid dotted import path. "
            f"Expected 'module.ClassName', "
            f"e.g. 'myapp.models.Article'."
        )
        raise ValueError(msg)

    module, _, class_name = dotted_path.rpartition(".")
    return module, class_name


def prefix_import(prefix: str, *parts: str) -> str:
    """Build a Python import path under *prefix* (which may be empty).

    Args:
        prefix: Optional package prefix, e.g. ``"_generated"``.
        *parts: Module name segments to join with ``.``.

    Returns:
        A ``.``-joined import path, with *prefix* prepended when
        non-empty.

    """
    if prefix:
        return ".".join([prefix, *parts])

    return ".".join(parts)
