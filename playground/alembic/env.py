"""Alembic environment for the be playground.

Imports all playground models so that Alembic autogenerate can detect
every table defined across the blog and inventory modules.

The database URL is read from the ``DATABASE_URL`` environment variable
at migration time; ``alembic.ini`` provides a fallback for development.

Migrations run synchronously via a standard psycopg2-compatible URL even
though the application uses an async engine.  This is the recommended
Alembic pattern: swap ``+asyncpg`` for ``+psycopg2`` (or the bare
``postgresql://`` dialect) when creating the migration engine.
"""

from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# ---------------------------------------------------------------------------
# Import all models so that Base.metadata is fully populated before
# autogenerate runs.  The order matters for foreign-key resolution.
# ---------------------------------------------------------------------------
import blog.models  # noqa: F401
import inventory.models  # noqa: F401

from db.base import Base

# ---------------------------------------------------------------------------
# Alembic config object — gives access to alembic.ini values.
# ---------------------------------------------------------------------------
config = context.config

# Set up Python logging from the config file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url(url: str) -> str:
    """Convert an async driver URL to a sync one for Alembic.

    Replaces ``+asyncpg`` with ``+psycopg2`` so that the migration
    engine uses a synchronous driver, which Alembic requires.

    Args:
        url: A SQLAlchemy database URL, possibly using an async driver.

    Returns:
        The same URL with the async driver replaced by a sync driver.
    """
    return re.sub(r"\+asyncpg\b", "+psycopg2", url)


def _get_url() -> str:
    """Resolve the database URL from the environment or alembic.ini.

    Returns:
        A synchronous-driver SQLAlchemy URL string.
    """
    raw = os.environ.get("DATABASE_URL") or config.get_main_option(
        "sqlalchemy.url", "postgresql+psycopg2://localhost/dev"
    )
    return _sync_url(raw)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection needed).

    Emits SQL to stdout instead of executing it, useful for review or
    applying manually in restricted environments.
    """
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""
    url = _get_url()
    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()

else:
    run_migrations_online()
