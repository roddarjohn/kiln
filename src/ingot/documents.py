"""Document storage primitives for kiln-generated FastAPI projects.

A *document* is a binary blob (image, PDF, attachment) tracked by a
metadata row in the consumer's database and a corresponding object
in S3-compatible storage.  This module ships three pieces:

* :class:`DocumentMixin` -- a SQLAlchemy 2.0 mixin that supplies
  the columns every document row needs (``id``, ``s3_key``,
  ``content_type``, ``size_bytes``, ``original_filename``,
  ``created_at``, ``uploaded_at``).  Consumers attach it to a
  concrete model on their own ``Base`` so the table lives in their
  metadata -- foreign keys, alembic, multi-schema setups all keep
  working.

* :class:`S3Storage` -- a small wrapper around ``boto3`` that
  exposes the three operations a presigned-upload flow actually
  needs: mint a presigned PUT URL, mint a presigned GET URL, delete
  an object.  The constructor takes explicit config so it's
  testable; :func:`default_storage` builds one from ``KILN_S3_*``
  env vars for the common case.

* Action functions -- :func:`make_request_upload`,
  :func:`complete_upload`, :func:`download`, and
  :func:`delete_document`.  These are shaped to plug into kiln's
  :class:`~kiln.operations.action.Action` operation: the consumer
  re-exports them from a project-local actions module and points
  ``resource.action`` entries at them.  The
  :class:`DocumentMixin`-typed object-action params match any
  concrete subclass via the introspector's supertype check, so the
  same four functions serve every document resource in the project.

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
from typing import TYPE_CHECKING, Any, cast, dataclass_transform

import boto3
from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_PRESIGN_TTL = 900
"""Presigned URL lifetime in seconds (15 min).

Long enough for a browser to PUT a multi-megabyte file over a slow
connection; short enough that a leaked URL stops working before it
shows up in logs anyone reads.
"""


# ``@dataclass_transform`` tells type checkers to treat the
# ``Mapped[...]`` class attributes below as if they were dataclass
# fields -- i.e. synthesize an ``__init__(*, id=..., s3_key=..., ...)``
# for static analysis purposes.  At runtime nothing changes: SQLAlchemy
# generates the real ``__init__`` when a concrete subclass is mapped
# via ``DeclarativeBase``.  Without this hint, a type checker sees the
# bare mixin as having no ``__init__`` (the annotations are class
# attributes, not constructor params) and rejects every kwarg the
# ``make_request_upload`` factory passes.  ``DeclarativeBase`` uses the
# same trick internally; we apply it to the mixin too so kwargs flow
# through statically.
@dataclass_transform(field_specifiers=(mapped_column,))
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
        default=lambda: datetime.datetime.now(tz=datetime.UTC),
    )
    """When the metadata row was created (PUT URL issued)."""

    uploaded_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the upload was confirmed.  ``None`` means pending --
    metadata exists but the blob may or may not be in S3."""


@dataclass
class S3Storage:
    """``boto3``-backed S3 client wrapper.

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


# --- Action request/response schemas --------------------------------------


class UploadRequest(BaseModel):
    """Body for the request-upload action.

    Carries everything :func:`make_request_upload` needs to reserve
    a key and bind the presigned PUT URL to the right content type.
    """

    filename: str
    content_type: str
    size_bytes: int


class UploadResponse(BaseModel):
    """Response for the request-upload action.

    The client PUTs the file bytes to ``upload_url`` (it must send
    a matching ``Content-Type`` header), then calls the
    complete-upload action with ``id`` to flip the row out of
    pending state.
    """

    id: uuid.UUID
    upload_url: str


class DownloadResponse(BaseModel):
    """Response for the download action -- a short-lived GET URL."""

    download_url: str


class DocumentResponse(BaseModel):
    """Pydantic projection of a :class:`DocumentMixin` row."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    s3_key: str
    content_type: str | None
    size_bytes: int | None
    original_filename: str | None
    created_at: datetime.datetime
    uploaded_at: datetime.datetime | None


# --- Action functions -----------------------------------------------------


def make_request_upload(
    model_cls: type[DocumentMixin],
) -> Callable[..., Awaitable[UploadResponse]]:
    """Build the request-upload collection action for *model_cls*.

    Returned as a factory because creating a row needs the concrete
    SQLAlchemy class; collection actions only receive ``db`` and
    ``body`` from the action handler, so the closure binds the
    class by reference.

    Consumers re-export the result from a project-local actions
    module and point a :func:`resource.action` entry at it -- the
    introspector reads this closure's signature, sees the
    :class:`UploadRequest` body and no model param, and emits a
    ``POST /upload`` route.
    """

    async def request_upload(
        *,
        db: AsyncSession,
        body: UploadRequest,
    ) -> UploadResponse:
        """Reserve a key and return a presigned PUT URL.

        The row is created with ``uploaded_at=NULL``; the client
        confirms the actual byte upload via :func:`complete_upload`.
        """
        document_id = uuid.uuid4()
        # Prefix the key with the document id so collisions on the
        # consumer-supplied filename can't reach across rows.
        key = f"{document_id.hex}/{body.filename}"
        # ``DocumentMixin`` carries ``@dataclass_transform`` so consumers
        # calling their own ``Attachment(id=..., s3_key=...)`` type-check
        # under mypy/pyright.  zuban (this project's checker) doesn't yet
        # honor ``dataclass_transform`` with SQLAlchemy ``mapped_column``
        # field specifiers, so we cast for the abstract reference here.
        # Runtime ``__init__`` is generated by SQLAlchemy on the mapped
        # subclass and accepts every column kwarg.
        document = cast("Any", model_cls)(
            id=document_id,
            s3_key=key,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            original_filename=body.filename,
        )
        db.add(document)
        # flush() is what makes the new row visible to a follow-up
        # complete_upload call inside the same transaction; the
        # action handler commits after the function returns.
        await db.flush()

        storage = default_storage()
        upload_url = storage.presigned_put_url(
            key,
            content_type=body.content_type,
        )
        return UploadResponse(
            id=document_id,
            upload_url=upload_url,
        )

    return request_upload


async def complete_upload(
    document: DocumentMixin,
    *,
    db: AsyncSession,  # noqa: ARG001 -- action handler passes this
) -> DocumentResponse:
    """Mark *document* as uploaded.

    Sets ``uploaded_at`` to now; the action handler commits the
    session after.  Idempotent -- calling twice just refreshes the
    timestamp, which is rare enough not to be worth a guard.
    """
    document.uploaded_at = datetime.datetime.now(tz=datetime.UTC)
    return DocumentResponse.model_validate(document)


async def download(
    document: DocumentMixin,
    *,
    db: AsyncSession,  # noqa: ARG001 -- action handler passes this
) -> DownloadResponse:
    """Return a presigned GET URL for *document*.

    Refuses with 404 when ``uploaded_at is None`` -- the row exists
    but the client never confirmed the PUT, so the object may not
    be in S3 and a presigned URL would just 404 noisily.
    """
    if document.uploaded_at is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document upload not complete",
        )
    storage = default_storage()
    return DownloadResponse(
        download_url=storage.presigned_get_url(document.s3_key),
    )


async def delete_document(
    document: DocumentMixin,
    *,
    db: AsyncSession,
) -> None:
    """Cascade-delete *document*: remove the S3 object then the row.

    Returns ``None`` so the action op emits 204 No Content -- the
    client doesn't need a body to know the row is gone.

    S3 first because :meth:`S3Storage.delete` is idempotent -- a
    crash between the two steps leaves an orphan row, which the
    next delete attempt cleans up.  Reversing the order would
    instead leak S3 objects, which are harder to find later.
    """
    storage = default_storage()
    storage.delete(document.s3_key)
    await db.delete(document)
