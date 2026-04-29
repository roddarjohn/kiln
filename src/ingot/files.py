"""File storage primitives for kiln-generated FastAPI projects.

This module's runtime dependency on ``boto3`` is gated behind the
``files`` extra.  Install with::

    pip install 'kiln-generator[files]'
    # or: uv add 'kiln-generator[files]'

Importing this module without the extra raises ``ModuleNotFoundError``
on ``import boto3`` -- so the gate is honest rather than lazy:
either the dep is there and everything works, or it isn't and the
import surface fails fast.

A *file* is a binary blob (image, PDF, attachment) tracked by a
metadata row in the consumer's database and a corresponding object
in S3-compatible storage.  This module ships three pieces:

* :class:`FileMixin` -- a pgcraft-compatible mixin supplying the
  six storage columns every file row needs (``s3_key``,
  ``content_type``, ``size_bytes``, ``original_filename``,
  ``created_at``, ``uploaded_at``).  Consumers subclass it on a
  pgcraft model and add a PK plugin (typically
  ``UUIDV4PKPlugin``) for the ``id`` column.

* :class:`S3Storage` -- a small wrapper around ``boto3`` that
  exposes the three operations a presigned-upload flow actually
  needs: mint a presigned PUT URL, mint a presigned GET URL, delete
  an object.  The constructor takes explicit config so it's
  testable; :func:`default_storage` builds one from ``KILN_S3_*``
  env vars for the common case.

* Action functions -- :func:`request_upload`,
  :func:`complete_upload`, :func:`download`, and :func:`delete_file`.
  These plug into be's
  :class:`~be.operations.action.Action` operation: the consumer
  points ``resource.action`` entries at them directly (no
  per-resource wrapper module).  The :class:`FileMixin`-typed
  parameters (instance for object actions, class for collection
  actions) match any concrete subclass via the introspector's
  supertype check, so the same four functions serve every file
  resource.
"""

import datetime
import os
import uuid
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

import boto3
from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    DateTime,
    String,
    delete,
    insert,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_PRESIGN_TTL = 900
"""Presigned URL lifetime in seconds (15 min).

Long enough for a browser to PUT a multi-megabyte file over a slow
connection; short enough that a leaked URL stops working before it
shows up in logs anyone reads.
"""


class FileMixin:
    """pgcraft mixin supplying the storage columns of a file record.

    Subclass on a pgcraft-mapped model alongside a PK plugin (the
    plugin owns ``id``):

    .. code-block:: python

        from ingot.files import FileMixin
        from pgcraft.factory import PGCraftSimple
        from pgcraft.plugins.pk import UUIDV4PKPlugin

        class Attachment(Base, FileMixin):
            __tablename__ = "attachments"
            __table_args__ = {"schema": "public"}
            __factory__ = PGCraftSimple
            __plugins__ = [UUIDV4PKPlugin()]

    The mixin deliberately doesn't declare ``id`` -- pgcraft's idiom
    is that primary keys are plugin-owned, and declaring it on the
    mixin would collide with the plugin's column at table-build time.
    The ``TYPE_CHECKING`` annotation below keeps ``file.id`` typed
    for the action helpers without committing to a column.

    A row with ``uploaded_at is None`` represents a file the
    server has reserved a key for (and handed the client a presigned
    PUT URL) but whose upload hasn't yet been confirmed.  Consumers
    typically clear or expire these rows on a schedule.
    """

    if TYPE_CHECKING:
        # Type-only -- the actual column comes from either
        # ``bind_file_model`` (plain SA) or the consumer's PK plugin
        # (pgcraft).  Declaring it as a mapped column here would
        # collide with the latter at table-build time.
        id: uuid.UUID

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

    Carries everything :func:`~ingot.files.request_upload` needs to reserve
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


# --- Action functions -----------------------------------------------------


async def request_upload(
    *,
    model_cls: type[FileMixin],
    db: AsyncSession,
    body: UploadRequest,
) -> UploadResponse:
    """Reserve a key and return a presigned PUT URL.

    The row is created with ``uploaded_at=NULL``; the client
    confirms the actual byte upload via :func:`complete_upload`.

    *model_cls* is supplied by the action handler, which detects
    the ``type[FileMixin]`` annotation and passes the resource's
    mapped class.  No per-resource factory binding needed --
    consumers point a resource's ``action`` config at this function
    directly.
    """
    file_id = uuid.uuid4()
    # Prefix the key with the file id so collisions on the
    # consumer-supplied filename can't reach across rows.
    key = f"{file_id.hex}/{body.filename}"
    await db.execute(
        insert(model_cls).values(
            id=file_id,
            s3_key=key,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            original_filename=body.filename,
        )
    )

    storage = default_storage()
    upload_url = storage.presigned_put_url(
        key,
        content_type=body.content_type,
    )
    return UploadResponse(
        id=file_id,
        upload_url=upload_url,
    )


async def complete_upload(
    file: FileMixin,
    *,
    db: AsyncSession,
) -> None:
    """Mark *file* as uploaded.

    Returns ``None`` so the action op emits 204 No Content -- a
    completed upload has no useful body to return; the client
    already knows the id.

    Issues a Core ``UPDATE`` rather than mutating the loaded ORM
    instance, so the persistence path is identical regardless of
    whether the caller's session has autoflush quirks.  Idempotent
    -- calling twice just refreshes the timestamp.
    """
    cls = type(file)
    await db.execute(
        update(cls)
        .where(cls.id == file.id)
        .values(uploaded_at=datetime.datetime.now(tz=datetime.UTC)),
    )


async def download(
    file: FileMixin,
    *,
    db: AsyncSession,  # noqa: ARG001 -- action handler passes this
) -> DownloadResponse:
    """Return a presigned GET URL for *file*.

    Refuses with 404 when ``uploaded_at is None`` -- the row exists
    but the client never confirmed the PUT, so the object may not
    be in S3 and a presigned URL would just 404 noisily.
    """
    if file.uploaded_at is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File upload not complete",
        )

    storage = default_storage()
    return DownloadResponse(
        download_url=storage.presigned_get_url(file.s3_key),
    )


async def delete_file(
    file: FileMixin,
    *,
    db: AsyncSession,
) -> None:
    """Cascade-delete *file*: remove the S3 object then the row.

    Returns ``None`` so the action op emits 204 No Content -- the
    client doesn't need a body to know the row is gone.

    S3 first because :meth:`S3Storage.delete` is idempotent -- a
    crash between the two steps leaves an orphan row, which the
    next delete attempt cleans up.  Reversing the order would
    instead leak S3 objects, which are harder to find later.
    """
    storage = default_storage()
    storage.delete(file.s3_key)
    cls = type(file)
    await db.execute(delete(cls).where(cls.id == file.id))
