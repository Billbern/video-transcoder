"""Unit tests for `S3Service` using `moto` to mock AWS S3 in-process."""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.services.storage import S3Service


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable boto3's network calls; force a deterministic region."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def moto_storage(aws_credentials: None):
    """An S3Service whose endpoint_url is unset so moto can intercept calls.

    Uses `mock_aws` as a context manager; the service is yielded inside the
    active mock so boto3 traffic is in-process.
    """
    from moto import mock_aws

    # Override the cached settings so endpoint_url is None and credentials are
    # moto-friendly.
    settings = get_settings()
    original_endpoint = settings.s3_endpoint
    original_ak = settings.s3_access_key
    original_sk = settings.s3_secret_key
    settings.s3_endpoint = None  # type: ignore[assignment]
    settings.s3_access_key = "testing"
    settings.s3_secret_key = "testing"

    with mock_aws():
        # Re-create the service so it picks up the new endpoint.
        svc = S3Service(settings)
        svc.ensure_buckets()
        try:
            yield svc
        finally:
            settings.s3_endpoint = original_endpoint
            settings.s3_access_key = original_ak
            settings.s3_secret_key = original_sk


def test_ensure_buckets_is_idempotent(moto_storage: S3Service) -> None:
    # Calling ensure_buckets again must not raise.
    moto_storage.ensure_buckets()
    moto_storage.ensure_buckets()


def test_upload_and_download(moto_storage: S3Service) -> None:
    moto_storage.upload_fileobj(
        bucket="raw-videos",
        key="jobs/abc/source.mp4",
        data=b"hello-bytes",
        content_type="video/mp4",
    )
    head = moto_storage.head_object(bucket="raw-videos", key="jobs/abc/source.mp4")
    assert head is not None
    assert head["ContentLength"] == len(b"hello-bytes")
    assert (
        moto_storage.object_exists(bucket="raw-videos", key="jobs/abc/source.mp4")
        is True
    )
    assert moto_storage.object_exists(bucket="raw-videos", key="missing") is False


def test_download_to_file(tmp_path, moto_storage: S3Service) -> None:
    moto_storage.upload_fileobj(bucket="raw-videos", key="k", data=b"x")
    dest = tmp_path / "out.bin"
    moto_storage.download_to_file(bucket="raw-videos", key="k", dest_path=str(dest))
    assert dest.read_bytes() == b"x"


def test_get_object_range(moto_storage: S3Service) -> None:
    moto_storage.upload_fileobj(bucket="raw-videos", key="k", data=b"abcdefghij")
    head = moto_storage.get_object_range(bucket="raw-videos", key="k", start=0, end=4)
    assert head == b"abcde"


def test_presigned_put_and_get(moto_storage: S3Service) -> None:
    put_url, expiry = moto_storage.generate_presigned_put(
        bucket="raw-videos",
        key="signed",
        content_type="video/mp4",
    )
    assert "raw-videos" in put_url or "signed" in put_url
    assert expiry == get_settings().s3_presign_expiry_seconds

    get_url, _ = moto_storage.generate_presigned_get(bucket="raw-videos", key="signed")
    assert get_url != put_url


def test_head_object_returns_none_for_missing(moto_storage: S3Service) -> None:
    assert moto_storage.head_object(bucket="raw-videos", key="nope") is None


def test_client_cached(moto_storage: S3Service) -> None:
    a = moto_storage.client()
    b = moto_storage.client()
    assert a is b
