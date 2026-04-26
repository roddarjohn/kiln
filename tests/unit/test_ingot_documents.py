"""Tests for ingot.documents."""

from __future__ import annotations

import datetime
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
    db.execute = AsyncMock()
    return db


def _executed_stmt(fake_db: MagicMock) -> object:
    """Return the single statement object passed to db.execute()."""
    fake_db.execute.assert_awaited_once()
    (stmt,) = fake_db.execute.await_args.args
    return stmt


async def test_request_upload_inserts_pending_row(fake_db, fake_storage):
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.sql.dml import Insert

    fake_storage.presigned_put_url.return_value = "https://s3/put"
    request_upload = make_request_upload(_Doc)

    body = UploadRequest(
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=1234,
    )
    response = await request_upload(db=fake_db, body=body)

    stmt = _executed_stmt(fake_db)
    assert isinstance(stmt, Insert)
    assert stmt.table.name == "_test_docs"
    params = stmt.compile(dialect=sqlite.dialect()).params
    assert params["id"] == response.id
    assert params["original_filename"] == "report.pdf"
    assert params["content_type"] == "application/pdf"
    assert params["size_bytes"] == 1234
    # Key includes the document id so colliding filenames don't share keys.
    assert params["s3_key"].startswith(f"{response.id.hex}/")
    assert params["s3_key"].endswith("/report.pdf")
    assert response.upload_url == "https://s3/put"
    fake_storage.presigned_put_url.assert_called_once_with(
        params["s3_key"],
        content_type="application/pdf",
    )


async def test_complete_upload_issues_update(fake_db):
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.sql.dml import Update

    doc = _Doc(s3_key="k", id=uuid.uuid4())

    result = await complete_upload(doc, db=fake_db)

    assert result is None  # 204 No Content
    stmt = _executed_stmt(fake_db)
    assert isinstance(stmt, Update)
    assert stmt.table.name == "_test_docs"
    params = stmt.compile(dialect=sqlite.dialect()).params
    assert isinstance(params["uploaded_at"], datetime.datetime)
    assert params["uploaded_at"].tzinfo is not None
    assert params["id_1"] == doc.id  # WHERE id = :id_1
    # The Python-side instance is NOT mutated.
    assert doc.uploaded_at is None


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
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.sql.dml import Delete

    doc = _Doc(s3_key="k", id=uuid.uuid4())

    result = await delete_document(doc, db=fake_db)

    assert result is None  # 204 No Content
    fake_storage.delete.assert_called_once_with("k")

    stmt = _executed_stmt(fake_db)
    assert isinstance(stmt, Delete)
    assert stmt.table.name == "_test_docs"
    params = stmt.compile(dialect=sqlite.dialect()).params
    assert params["id_1"] == doc.id


async def test_delete_document_s3_first_then_row(fake_storage):
    """S3 delete must precede the SQL delete -- crash leaves an
    orphan row (recoverable), not a leaked S3 object."""
    call_order: list[str] = []

    db = MagicMock()
    db.execute = AsyncMock(side_effect=lambda *_: call_order.append("db"))
    fake_storage.delete.side_effect = lambda *_: call_order.append("s3")

    doc = _Doc(s3_key="k", id=uuid.uuid4())
    await delete_document(doc, db=db)

    assert call_order == ["s3", "db"]
