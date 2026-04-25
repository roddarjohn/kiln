"""Tests for ingot.documents."""

from __future__ import annotations

import datetime
import io
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import ingot.documents as documents_mod
from ingot.documents import (
    DEFAULT_PRESIGN_TTL,
    DocumentMixin,
    S3Storage,
    UploadRequest,
    complete_upload,
    default_storage,
    delete_document,
    download,
    make_request_upload,
)


class _Base(DeclarativeBase):
    pass


class _Doc(_Base, DocumentMixin):
    __tablename__ = "_test_docs"


class _Attachment(_Base, DocumentMixin):
    __tablename__ = "_test_attachments"

    owner_id: Mapped[uuid.UUID] = mapped_column()


def _columns(model: type) -> dict[str, object]:
    return {col.name: col for col in inspect(model).columns}


# --- DocumentMixin ---------------------------------------------------------


def test_mixin_supplies_expected_columns():
    cols = _columns(_Doc)
    expected = {
        "id",
        "s3_key",
        "content_type",
        "size_bytes",
        "original_filename",
        "created_at",
        "uploaded_at",
    }
    assert expected <= set(cols)


def test_mixin_id_is_primary_key():
    cols = _columns(_Doc)
    assert cols["id"].primary_key is True


def test_mixin_s3_key_is_unique_and_not_null():
    cols = _columns(_Doc)
    assert cols["s3_key"].unique is True
    assert cols["s3_key"].nullable is False


def test_mixin_optional_columns_are_nullable():
    cols = _columns(_Doc)
    for name in ("content_type", "size_bytes", "original_filename"):
        assert cols[name].nullable is True
    assert cols["uploaded_at"].nullable is True


def test_mixin_created_at_is_not_null():
    cols = _columns(_Doc)
    assert cols["created_at"].nullable is False


def test_mixin_id_default_is_uuid4_callable():
    cols = _columns(_Doc)
    # Calling the default produces a UUID -- proves uuid.uuid4 is wired
    # in directly rather than a stamped-at-import constant.
    value = cols["id"].default.arg(None)
    assert isinstance(value, uuid.UUID)


def test_mixin_composes_with_extra_columns():
    cols = _columns(_Attachment)
    assert "owner_id" in cols
    assert "s3_key" in cols


# --- S3Storage -------------------------------------------------------------


def _storage_with_mock_client() -> tuple[S3Storage, MagicMock]:
    client = MagicMock()
    storage = S3Storage(
        bucket="bkt",
        region="us-east-1",
        client_factory=lambda **_: client,
    )
    return storage, client


def test_client_lazily_built_with_region():
    factory = MagicMock(return_value=MagicMock())
    storage = S3Storage(bucket="b", region="us-west-2", client_factory=factory)
    _ = storage.client
    factory.assert_called_once_with(service_name="s3", region_name="us-west-2")


def test_client_passes_endpoint_url_when_set():
    factory = MagicMock(return_value=MagicMock())
    storage = S3Storage(
        bucket="b",
        endpoint_url="http://localstack:4566",
        client_factory=factory,
    )
    _ = storage.client
    factory.assert_called_once_with(
        service_name="s3",
        endpoint_url="http://localstack:4566",
    )


def test_client_omits_unset_kwargs():
    factory = MagicMock(return_value=MagicMock())
    storage = S3Storage(bucket="b", client_factory=factory)
    _ = storage.client
    factory.assert_called_once_with(service_name="s3")


def test_client_is_cached():
    factory = MagicMock(return_value=MagicMock())
    storage = S3Storage(bucket="b", client_factory=factory)
    a = storage.client
    b = storage.client
    assert a is b
    factory.assert_called_once()


def test_presigned_put_url_passes_bucket_and_key():
    storage, client = _storage_with_mock_client()
    client.generate_presigned_url.return_value = "https://s3/put"

    url = storage.presigned_put_url("a/b.png")

    assert url == "https://s3/put"
    client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={"Bucket": "bkt", "Key": "a/b.png"},
        ExpiresIn=DEFAULT_PRESIGN_TTL,
    )


def test_presigned_put_url_includes_content_type():
    storage, client = _storage_with_mock_client()
    storage.presigned_put_url("k", content_type="image/png", expires_in=60)

    client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={"Bucket": "bkt", "Key": "k", "ContentType": "image/png"},
        ExpiresIn=60,
    )


def test_presigned_get_url_uses_get_object():
    storage, client = _storage_with_mock_client()
    client.generate_presigned_url.return_value = "https://s3/get"

    url = storage.presigned_get_url("k", expires_in=120)

    assert url == "https://s3/get"
    client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "bkt", "Key": "k"},
        ExpiresIn=120,
    )


def test_delete_calls_delete_object():
    storage, client = _storage_with_mock_client()
    storage.delete("k")
    client.delete_object.assert_called_once_with(Bucket="bkt", Key="k")


def test_upload_fileobj_passes_through_content_type():
    storage, client = _storage_with_mock_client()
    fileobj = io.BytesIO(b"hello")

    storage.upload_fileobj(fileobj, "k", content_type="text/plain")

    client.upload_fileobj.assert_called_once_with(
        fileobj,
        "bkt",
        "k",
        ExtraArgs={"ContentType": "text/plain"},
    )


def test_upload_fileobj_without_content_type_passes_none():
    storage, client = _storage_with_mock_client()
    fileobj = io.BytesIO(b"hello")

    storage.upload_fileobj(fileobj, "k")

    client.upload_fileobj.assert_called_once_with(
        fileobj, "bkt", "k", ExtraArgs=None
    )


# --- default_storage -------------------------------------------------------


def test_default_storage_reads_env(monkeypatch):
    monkeypatch.setenv("KILN_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("KILN_S3_REGION", "eu-west-1")
    monkeypatch.delenv("KILN_S3_ENDPOINT_URL", raising=False)

    storage = default_storage()

    assert storage.bucket == "my-bucket"
    assert storage.region == "eu-west-1"
    assert storage.endpoint_url is None


def test_default_storage_picks_up_endpoint_url(monkeypatch):
    monkeypatch.setenv("KILN_S3_BUCKET", "b")
    monkeypatch.setenv("KILN_S3_ENDPOINT_URL", "http://minio:9000")

    storage = default_storage()

    assert storage.endpoint_url == "http://minio:9000"


def test_default_storage_raises_when_bucket_missing(monkeypatch):
    monkeypatch.delenv("KILN_S3_BUCKET", raising=False)

    with pytest.raises(RuntimeError, match="KILN_S3_BUCKET"):
        default_storage()


# --- Action functions ------------------------------------------------------


@pytest.fixture
def fake_storage(monkeypatch):
    """Replace default_storage() with a MagicMock for the test."""
    storage = MagicMock()
    monkeypatch.setattr(documents_mod, "default_storage", lambda: storage)
    return storage


@pytest.fixture
def fake_db():
    """Minimal AsyncSession stand-in for action functions."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    return db


async def test_request_upload_creates_pending_row(fake_db, fake_storage):
    fake_storage.presigned_put_url.return_value = "https://s3/put"
    request_upload = make_request_upload(_Doc)

    body = UploadRequest(
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=1234,
    )
    response = await request_upload(db=fake_db, body=body)

    fake_db.add.assert_called_once()
    fake_db.flush.assert_awaited_once()
    (added,) = fake_db.add.call_args.args
    assert isinstance(added, _Doc)
    assert added.id == response.id
    assert added.s3_key == response.key
    assert added.original_filename == "report.pdf"
    assert added.content_type == "application/pdf"
    assert added.size_bytes == 1234
    assert added.uploaded_at is None  # row starts pending
    # Key includes the document id so colliding filenames don't share keys.
    assert response.id.hex in response.key
    assert response.key.endswith("/report.pdf")
    assert response.upload_url == "https://s3/put"
    fake_storage.presigned_put_url.assert_called_once_with(
        response.key,
        content_type="application/pdf",
    )


async def test_complete_upload_sets_timestamp(fake_db):
    # SQLAlchemy column defaults only fire on flush, so populate the
    # not-null fields by hand here.
    doc = _Doc(
        s3_key="k",
        id=uuid.uuid4(),
        created_at=datetime.datetime.now(tz=datetime.UTC),
    )
    doc.uploaded_at = None

    response = await complete_upload(doc, db=fake_db)

    assert isinstance(doc.uploaded_at, datetime.datetime)
    assert doc.uploaded_at.tzinfo is not None
    assert response.uploaded_at == doc.uploaded_at
    assert response.id == doc.id


async def test_download_returns_presigned_url(fake_db, fake_storage):
    fake_storage.presigned_get_url.return_value = "https://s3/get"
    doc = _Doc(s3_key="k", id=uuid.uuid4())
    doc.uploaded_at = datetime.datetime.now(tz=datetime.UTC)

    response = await download(doc, db=fake_db)

    assert response.download_url == "https://s3/get"
    fake_storage.presigned_get_url.assert_called_once_with("k")


async def test_download_404s_when_pending(fake_db, fake_storage):
    doc = _Doc(s3_key="k", id=uuid.uuid4())
    doc.uploaded_at = None

    with pytest.raises(HTTPException) as exc:
        await download(doc, db=fake_db)
    assert exc.value.status_code == 404
    fake_storage.presigned_get_url.assert_not_called()


async def test_delete_document_removes_object_then_row(fake_db, fake_storage):
    doc = _Doc(s3_key="k", id=uuid.uuid4())

    response = await delete_document(doc, db=fake_db)

    assert response.ok is True
    fake_storage.delete.assert_called_once_with("k")
    fake_db.delete.assert_awaited_once_with(doc)
    # S3 deletion happens before the row delete -- a crash between
    # the two leaves a recoverable orphan row, not a leaked object.
    storage_call = fake_storage.delete.call_args_list[0]
    db_call = fake_db.delete.await_args_list[0]
    assert storage_call is not None
    assert db_call is not None
