"""Rate-limiting primitives for kiln-generated FastAPI projects.

This module's runtime dependency on ``slowapi`` and ``limits`` is
gated behind the ``rate-limit`` extra.  Install with::

    pip install 'kiln-generator[rate-limit]'
    # or: uv add 'kiln-generator[rate-limit]'

The pieces:

* :class:`RateLimitBucketMixin` -- a SQLAlchemy mixin supplying the
  three columns every counter row needs (``key``, ``hits``,
  ``expires_at``).  Same idiom as :class:`ingot.files.FileMixin`:
  the consumer subclasses it on their own model so they own the
  table and we own the columns.

* :class:`PostgresStorage` -- a ``limits``-compatible synchronous
  storage backend backed by a small dedicated SQLAlchemy engine.
  ``slowapi``'s enforcement path calls ``limiter.hit(...)``
  synchronously (not awaited) so an async storage cannot satisfy
  it; we use a separate sync engine targeting the same Postgres
  database the rest of the app talks to.

* :func:`build_limiter` -- factory that constructs a slowapi
  ``slowapi.Limiter`` and wires our :class:`PostgresStorage`
  in as its backing store, swapping out the placeholder
  ``memory://`` storage slowapi creates internally.

* :func:`default_key_func` -- the per-request rate-limit key
  callable used by default (client IP).
"""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING, Any

from limits.storage import Storage
from limits.strategies import STRATEGIES
from slowapi import Limiter
from sqlalchemy import (
    BigInteger,
    DateTime,
    String,
    case,
    create_engine,
    delete,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from fastapi import Request
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


class RateLimitBucketMixin:
    """SQLAlchemy mixin supplying the columns of a rate-limit bucket.

    Subclass on a regular SQLAlchemy ``Base`` to carry the storage
    columns:

    .. code-block:: python

        from ingot.rate_limit import RateLimitBucketMixin

        class RateLimitBucket(Base, RateLimitBucketMixin):
            __tablename__ = "rate_limit_buckets"

    Unlike :class:`ingot.files.FileMixin`, the natural primary key
    here is :attr:`key` itself (the limit identifier produced by the
    ``key_func`` plus the limit string).  Declaring it
    ``primary_key=True`` means the consumer doesn't need to bring
    their own PK plugin to use this mixin.

    The consumer is responsible for migrating the table; ``be``
    doesn't generate Alembic migrations.
    """

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    """Rate-limit key.  ``slowapi`` builds this from the route, the
    ``key_func`` output, and the limit string."""

    hits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    """Counter value for the current window."""

    expires_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    """When the current window ends.  Rows with
    ``expires_at < now()`` are stale and reset on the next hit."""


def default_key_func(request: Request) -> str:
    """Default rate-limit key: client IP, falling back to ``unknown``.

    Used when :attr:`~be.config.schema.RateLimitConfig.key_func` is
    not configured.  Behind a trusted proxy you almost certainly
    want to point ``key_func`` at a function that reads
    ``X-Forwarded-For`` instead -- this default deliberately
    refuses to trust any header.
    """
    if request.client is None:
        return "unknown"

    return request.client.host


class PostgresStorage(Storage):
    """``limits``-compatible storage backed by a sync Postgres engine.

    slowapi's enforcement path is synchronous (``limiter.hit(...)``
    is *not* awaited), so an async storage cannot satisfy it.  This
    class uses a dedicated synchronous SQLAlchemy engine pointed at
    the same Postgres database as the rest of the app -- separate
    connection pool, same data.

    The counter row is upserted with Postgres ``INSERT ... ON
    CONFLICT DO UPDATE``: a hit on a fresh window inserts a row;
    a hit on an active window increments :attr:`~RateLimitBucketMixin.hits`;
    a hit on an expired window resets :attr:`~RateLimitBucketMixin.hits`
    to ``amount`` and shifts :attr:`~RateLimitBucketMixin.expires_at`
    forward.
    """

    STORAGE_SCHEME = ["postgres-rate-limit"]  # noqa: RUF012
    """URI scheme this storage registers under.  Not used for
    instantiation -- :func:`build_limiter` constructs the storage
    directly and patches it onto the slowapi limiter -- but
    ``limits`` requires the attribute on every storage subclass.

    Declared as an instance attribute (not :data:`~typing.ClassVar`)
    to mirror the base ``Storage`` class -- ``limits`` annotates it
    as such and a ``ClassVar`` override would conflict at type-check
    time.
    """

    def __init__(
        self,
        *,
        model: type[RateLimitBucketMixin],
        session_maker: Callable[[], Session],
    ) -> None:
        """Build a Postgres-backed storage.

        Args:
            model: The consumer's bucket model class (must mix in
                :class:`RateLimitBucketMixin`).
            session_maker: Zero-arg callable returning a sync
                SQLAlchemy ``Session`` (typically a configured
                ``sessionmaker``).

        """
        # ``Storage.__init__`` accepts a URI-like string for the
        # registry-driven path; we never go through that path so any
        # value is fine, including ``None``.
        super().__init__(uri=None, wrap_exceptions=False)
        self.model = model
        self._session_maker = session_maker

    @property
    def base_exceptions(
        self,
    ) -> type[Exception] | tuple[type[Exception], ...]:
        """Exception class(es) ``limits`` should treat as storage failures."""
        return SQLAlchemyError

    def _now(self) -> _dt.datetime:
        return _dt.datetime.now(tz=_dt.UTC)

    def incr(self, key: str, expiry: int, amount: int = 1) -> int:
        """Increment *key* by *amount*, opening a fresh window when stale.

        Args:
            key: Rate-limit key.
            expiry: Window duration in seconds.
            amount: Increment step (defaults to 1).

        Returns:
            The new counter value after the increment.

        """
        now = self._now()
        new_expiry = now + _dt.timedelta(seconds=expiry)
        cls = self.model

        with self._session_maker() as session:
            stmt = (
                pg_insert(cls)
                .values(
                    key=key,
                    hits=amount,
                    expires_at=new_expiry,
                )
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={
                        # Reset the counter (and shift the window
                        # forward) when the existing row is stale;
                        # otherwise just add to it.
                        "hits": _case_when_expired(cls, now, amount),
                        "expires_at": _case_when_expired_expiry(
                            cls, now, new_expiry
                        ),
                    },
                )
                .returning(cls.hits)
            )
            result = session.execute(stmt).scalar_one()
            session.commit()
            return int(result)

    def get(self, key: str) -> int:
        """Return the current counter value for *key* (0 when stale)."""
        now = self._now()
        cls = self.model

        with self._session_maker() as session:
            stmt = select(cls.hits, cls.expires_at).where(cls.key == key)
            row = session.execute(stmt).first()

        if row is None:
            return 0

        hits, expires_at = row

        if expires_at < now:
            return 0

        return int(hits)

    def get_expiry(self, key: str) -> float:
        """Return the window expiry for *key* as a UNIX timestamp.

        ``limits`` treats a value in the past as "no active window".
        """
        cls = self.model

        with self._session_maker() as session:
            stmt = select(cls.expires_at).where(cls.key == key)
            expires_at = session.execute(stmt).scalar_one_or_none()

        if expires_at is None:
            return self._now().timestamp()

        return expires_at.timestamp()

    def check(self) -> bool:
        """Return whether the storage is reachable.

        ``limits`` calls this opportunistically when a previous call
        raised; we keep it cheap by issuing ``SELECT 1`` rather than
        touching the bucket table.
        """
        try:
            with self._session_maker() as session:
                session.execute(text("SELECT 1"))

        except SQLAlchemyError:
            return False

        return True

    def reset(self) -> int | None:
        """Delete every counter row.  Returns the number deleted."""
        cls = self.model

        with self._session_maker() as session:
            result = session.execute(delete(cls))
            session.commit()
            # ``execute`` returns a CursorResult for DML statements,
            # whose ``rowcount`` is the number of affected rows.  The
            # static return type is the broader ``Result`` so we
            # access it via getattr to avoid a type-check false
            # positive without sacrificing the runtime behaviour.
            rowcount: int | None = getattr(result, "rowcount", None)
            return rowcount

    def clear(self, key: str) -> None:
        """Delete the counter row for *key* (no-op when absent)."""
        cls = self.model

        with self._session_maker() as session:
            session.execute(delete(cls).where(cls.key == key))
            session.commit()


def _case_when_expired(
    cls: type[RateLimitBucketMixin],
    now: _dt.datetime,
    amount: int,
) -> Any:
    """Return a CASE: ``amount`` when stale, else ``hits + amount``."""
    return case(
        (cls.expires_at < now, amount),
        else_=cls.hits + amount,
    )


def _case_when_expired_expiry(
    cls: type[RateLimitBucketMixin],
    now: _dt.datetime,
    new_expiry: _dt.datetime,
) -> Any:
    """Return a CASE that shifts ``expires_at`` forward only when stale."""
    return case(
        (cls.expires_at < now, new_expiry),
        else_=cls.expires_at,
    )


def build_limiter(
    *,
    model: type[RateLimitBucketMixin],
    sync_url: str,
    key_func: Callable[[Request], str] | None = None,
    default_limits: Iterable[str] = (),
    headers_enabled: bool = True,
    engine: Engine | None = None,
) -> Limiter:
    """Build a slowapi ``slowapi.Limiter`` backed by Postgres.

    The returned limiter has its ``_storage`` and ``_limiter``
    fields swapped out for our :class:`PostgresStorage` -- slowapi
    constructs a placeholder ``memory://`` storage internally
    because its public API only takes a URI, and we replace it
    rather than going through URI dispatch (the storage needs
    Python objects -- the bucket model and a sessionmaker -- that
    don't round-trip through a URI).

    Args:
        model: The consumer's bucket model class (must mix in
            :class:`RateLimitBucketMixin`).
        sync_url: A *synchronous* Postgres DSN for the
            rate-limit storage.  The app's main async DSN
            (``postgresql+asyncpg://...``) is fine to reuse with
            the ``+asyncpg`` driver tag stripped.
        key_func: Per-request key callable.  Defaults to
            :func:`default_key_func` (client IP).
        default_limits: Iterable of limit strings applied to every
            route that doesn't have its own ``@limiter.limit(...)``.
        headers_enabled: Whether slowapi emits ``X-RateLimit-*``
            response headers.
        engine: Pre-built sync engine.  Optional escape hatch for
            tests / custom pools; production callers leave it
            ``None`` and let the helper build one from *sync_url*.

    Returns:
        A configured slowapi ``slowapi.Limiter``.

    """
    if engine is None:
        engine = create_engine(sync_url, future=True, pool_pre_ping=True)

    session_maker = sessionmaker(engine, expire_on_commit=False)
    storage = PostgresStorage(model=model, session_maker=session_maker)

    limiter = Limiter(
        key_func=key_func or default_key_func,
        default_limits=list(default_limits),
        headers_enabled=headers_enabled,
        # Placeholder; we replace ``_storage`` below.  slowapi's
        # ``__init__`` insists on building one storage up front.
        storage_uri="memory://",
    )

    # Swap in the real storage and rebuild the strategy that wraps
    # it.  ``fixed-window`` matches slowapi's default strategy.
    limiter._storage = storage  # noqa: SLF001
    limiter._limiter = STRATEGIES["fixed-window"](storage)  # noqa: SLF001
    return limiter
