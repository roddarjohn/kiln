"""Document storage primitives for kiln-generated FastAPI projects.

A *document* is a binary blob (image, PDF, attachment) tracked by a
metadata row in the consumer's database and a corresponding object
in S3-compatible storage.  This module ships two pieces:

* :class:`DocumentMixin` -- a SQLAlchemy 2.0 mixin that supplies
  the columns every document row needs (``id``, ``s3_key``,
  ``content_type``, ``size_bytes``, ``original_filename``,
  ``created_at``, ``uploaded_at``).  Consumers attach it to a
  concrete model on their own ``Base`` so the table lives in their
  metadata -- foreign keys, alembic, multi-schema setups all keep
  working.

* :class:`S3Storage` -- a small wrapper around ``boto3`` that
  exposes the operations a presigned-upload flow actually needs:
  mint a presigned PUT URL, mint a presigned GET URL, delete an
  object.  The constructor takes explicit config so it's testable;
  :func:`default_storage` builds one from ``KILN_S3_*`` env vars
  for the common case.

The split is deliberate: the mixin is pure SQLAlchemy and has no
runtime dependency on AWS, so consumers who only want the metadata
shape (e.g. for migrations) don't pay for the storage client.
"""

from __future__ import annotations

import datetime
import os
import uuid
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol

import boto3
from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO


DEFAULT_PRESIGN_TTL = 900
"""Presigned URL lifetime in seconds (15 min).

Long enough for a browser to PUT a multi-megabyte file over a slow
connection; short enough that a leaked URL stops working before it
shows up in logs anyone reads.
"""


def _utcnow() -> datetime.datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Wrapped so :class:`DocumentMixin`'s column default points at a
    callable that tests can monkey-patch, rather than capturing
    ``datetime.now`` at import time.
    """
    return datetime.datetime.now(tz=datetime.UTC)


class DocumentMixin:
    """SQLAlchemy mixin supplying the columns of a document record.

    Add to a concrete model on the consumer's ``Base``:

    .. code-block:: python

        class Attachment(Base, DocumentMixin):
            __tablename__ = "attachments"
            owner_id: Mapped[uuid.UUID] = mapped_column(
                ForeignKey("users.id"),
            )

    A row with ``uploaded_at is None`` represents a document the
    server has reserved a key for (and handed the client a presigned
    PUT URL) but whose upload hasn't yet been confirmed.  Consumers
    typically clear or expire these rows on a schedule.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    """Surrogate primary key.  UUIDv4 so it's safe to expose."""

    s3_key: Mapped[str] = mapped_column(String(1024), unique=True)
    """Object key in the storage bucket.  Unique so a row maps to
    exactly one blob; collision is a programming error, not a race."""

    content_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    """MIME type the client declared at upload time, when known."""

    size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    """Object size in bytes after the upload completes.  ``BigInteger``
    because ``Integer`` tops out around 2 GiB and large media uploads
    blow past that."""

    original_filename: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    """Filename the client supplied; useful for ``Content-Disposition``
    on download.  Not used for storage -- the canonical name is
    :attr:`s3_key`."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
    )
    """When the metadata row was created (PUT URL issued)."""

    uploaded_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the upload was confirmed.  ``None`` means pending --
    metadata exists but the blob may or may not be in S3."""


class Storage(Protocol):
    """Storage backend interface.

    :class:`S3Storage` is the only implementation shipped, but the
    Protocol exists so tests and alternative backends (in-memory,
    filesystem, GCS) plug in without subclassing.
    """

    def presigned_put_url(
        self,
        key: str,
        *,
        expires_in: int = DEFAULT_PRESIGN_TTL,
        content_type: str | None = None,
    ) -> str:
        """Return a URL the client can PUT to upload an object."""
        ...

    def presigned_get_url(
        self,
        key: str,
        *,
        expires_in: int = DEFAULT_PRESIGN_TTL,
    ) -> str:
        """Return a URL the client can GET to download an object."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object at *key*.  Idempotent."""
        ...

    def upload_fileobj(
        self,
        fileobj: BinaryIO,
        key: str,
        *,
        content_type: str | None = None,
    ) -> None:
        """Stream *fileobj* directly to *key* (server-mediated upload)."""
        ...


@dataclass
class S3Storage:
    """``boto3``-backed implementation of :class:`Storage`.

    The constructor takes explicit config so tests can build an
    instance pointed at a stub or a localstack endpoint without
    setting env vars.  :func:`default_storage` is the env-driven
    factory for production use.

    ``client_factory`` is plumbed through so tests can inject a
    ``MagicMock`` instead of a real ``boto3.client``.
    """

    bucket: str
    region: str | None = None
    endpoint_url: str | None = None
    client_factory: Callable[..., Any] = field(default=boto3.client)

    @cached_property
    def client(self) -> Any:
        """Lazily-built ``boto3`` S3 client.

        Cached so a single :class:`S3Storage` instance reuses one
        connection pool across calls.
        """
        kwargs: dict[str, Any] = {"service_name": "s3"}
        if self.region is not None:
            kwargs["region_name"] = self.region
        if self.endpoint_url is not None:
            kwargs["endpoint_url"] = self.endpoint_url
        return self.client_factory(**kwargs)

    def presigned_put_url(
        self,
        key: str,
        *,
        expires_in: int = DEFAULT_PRESIGN_TTL,
        content_type: str | None = None,
    ) -> str:
        """Mint a presigned PUT URL for *key*.

        When *content_type* is supplied, the client must send a
        matching ``Content-Type`` header on the PUT or S3 rejects
        the request -- this binds the upload to the type the row
        was created for.
        """
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if content_type is not None:
            params["ContentType"] = content_type
        url = self.client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return str(url)

    def presigned_get_url(
        self,
        key: str,
        *,
        expires_in: int = DEFAULT_PRESIGN_TTL,
    ) -> str:
        """Mint a presigned GET URL for *key*."""
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return str(url)

    def delete(self, key: str) -> None:
        """Delete the object at *key*.

        S3's ``DeleteObject`` is idempotent -- deleting a missing
        key returns 204 the same as deleting an existing one -- so
        callers don't need to guard against double-delete races.
        """
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def upload_fileobj(
        self,
        fileobj: BinaryIO,
        key: str,
        *,
        content_type: str | None = None,
    ) -> None:
        """Stream *fileobj* through the app server to S3.

        Use this only when the client can't be trusted with a
        presigned URL (e.g. server-side imports, generated reports).
        Production user uploads should go through
        :meth:`presigned_put_url` so bytes never touch the app box.
        """
        extra: dict[str, Any] = {}
        if content_type is not None:
            extra["ContentType"] = content_type
        self.client.upload_fileobj(
            fileobj,
            self.bucket,
            key,
            ExtraArgs=extra or None,
        )


def default_storage() -> S3Storage:
    """Build an :class:`S3Storage` from ``KILN_S3_*`` env vars.

    Reads:

    * ``KILN_S3_BUCKET`` -- bucket name (required).
    * ``KILN_S3_REGION`` -- AWS region; optional, falls back to the
      boto3 default chain.
    * ``KILN_S3_ENDPOINT_URL`` -- override for MinIO / localstack /
      non-AWS S3-compatible endpoints; optional.

    Raises:
        RuntimeError: When ``KILN_S3_BUCKET`` is not set.

    """
    bucket = os.environ.get("KILN_S3_BUCKET")
    if not bucket:
        msg = "KILN_S3_BUCKET environment variable is required"
        raise RuntimeError(msg)
    return S3Storage(
        bucket=bucket,
        region=os.environ.get("KILN_S3_REGION"),
        endpoint_url=os.environ.get("KILN_S3_ENDPOINT_URL"),
    )
