"""Query function for the published_articles view.

Kiln imports ``get_query`` from here and calls it from the generated route.
"""

from __future__ import annotations

from sqlalchemy import select

from blog.models.article import Article
from blog.models.author import Author


def get_query():
    """Return published articles joined with their author name."""
    return (
        select(
            Article.id,
            Article.title,
            Article.slug,
            Author.name.label("author_name"),
            Article.created_at.label("published_at"),
        )
        .join(Author, Article.author_id == Author.id)
        .where(Article.published.is_(True))
        .order_by(Article.created_at.desc())
    )
