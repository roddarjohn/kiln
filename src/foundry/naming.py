"""Naming conventions and import-path helpers for code generation.

Provides :class:`Name` for deriving conventional identifiers
(PascalCase, snake_case, slugs) from a base string, plus helpers
for splitting and constructing Python dotted import paths.
"""

import re

_PASCAL_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z\d])([A-Z])")


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
        """Fully lowercased form, no separator inserted.

        ``"Article"`` → ``"article"``,
        ``"publish_article"`` → ``"publish_article"``.

        Use :attr:`snake` instead when the result will become a
        file/module/function name and the input is PascalCase --
        ``Name("NotificationPreference").lower`` collapses to
        ``"notificationpreference"`` whereas :attr:`snake` returns
        ``"notification_preference"``.
        """
        return self.raw.lower()

    @property
    def snake(self) -> str:
        """snake_case form (for file/module/function names).

        ``"Article"`` → ``"article"``,
        ``"NotificationPreference"`` → ``"notification_preference"``,
        ``"XMLParser"`` → ``"xml_parser"``,
        ``"publish_article"`` → ``"publish_article"``.

        PascalCase / camelCase boundaries become underscores so
        multi-word model classes produce readable identifiers.
        Strings already containing underscores pass through
        unchanged (only lowercased) on the assumption that the
        caller chose their own boundaries.
        """
        if "_" in self.raw:
            return self.raw.lower()

        head = _PASCAL_BOUNDARY.sub(r"\1_\2", self.raw)
        return _CAMEL_BOUNDARY.sub(r"\1_\2", head).lower()

    @property
    def slug(self) -> str:
        """Hyphenated slug form (for URL segments).

        ``"publish_article"`` → ``"publish-article"``,
        ``"NotificationPreference"`` → ``"notification-preference"``
        (PascalCase boundaries are split first via :attr:`snake`).
        """
        return self.snake.replace("_", "-")

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
            ``("myapp.models", Name("Article"))``.  Callers that
            need the raw class-name string can read it from
            ``Name.raw``.

        Raises:
            ValueError: If *dotted_path* contains fewer than two
                parts.

        """
        if "." not in dotted_path:
            msg = (
                f"'{dotted_path}' is not a valid dotted import path. "
                f"Expected 'module.ClassName', "
                f"e.g. 'myapp.models.Article'."
            )
            raise ValueError(msg)

        module, _, class_name = dotted_path.rpartition(".")
        return module, cls(class_name)

    @staticmethod
    def parent_path(dotted: str, *, levels: int = 1) -> str:
        """Return *dotted*'s ancestor module path.

        Drops the last *levels* dot-separated segments.  When the
        path runs out of segments before *levels* are stripped, it
        stops and returns whatever remains rather than producing
        an empty string — so callers can chain the op without
        guarding for short paths::

            >>> Name.parent_path("blog.models.Article")
            'blog.models'
            >>> Name.parent_path("blog.models.Article", levels=2)
            'blog'
            >>> Name.parent_path("single", levels=2)
            'single'

        ``levels=2`` is the common idiom for "app module from
        model dotted path" -- ``"blog.models.Article" -> "blog"``.
        """
        for _ in range(levels):
            head, sep, _ = dotted.rpartition(".")

            if not sep:
                return dotted

            dotted = head

        return dotted


def split_dotted_class(dotted_path: str) -> tuple[str, str]:
    """Split a fully-qualified class path into ``(module, class_name)``.

    Convenience for callers that just want both pieces as strings —
    skip the :class:`Name` wrapper that :meth:`Name.from_dotted`
    returns when the only thing they need is the bare class-name
    string for an import line.

    Args:
        dotted_path: Fully-qualified class path, e.g.
            ``"myapp.models.Article"``.

    Returns:
        ``(module, class_name)`` tuple of plain strings.

    Raises:
        ValueError: If *dotted_path* contains fewer than two parts.

    """
    module, name = Name.from_dotted(dotted_path)
    return module, name.raw


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
