"""Object storage service (S3-compatible via boto3).

Two buckets are used (per `docs/prd.md`):
  - Bucket A (`s3_bucket_ingest`)  - raw uploads, infrequent access.
  - Bucket B (`s3_bucket_output`) - processed outputs, optimized for read.

In Sprint 1 the worker reads/writes objects via this service. In Sprint 2 we
add presigned-URL issuance to the API layer.
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError, EndpointConnectionError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """Raised when the object-storage backend cannot complete an operation."""


class S3Service:
    """Thin wrapper around boto3 for our two buckets.

    Lazy: the boto3 client is only created on first use, so tests that don't
    touch storage don't have to mock it.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None
        self._buckets_ensured = False

    # -- Setup ----------------------------------------------------------------

    def _build_client(self) -> Any:
        return boto3.client(
            "s3",
            endpoint_url=self._settings.s3_endpoint,
            region_name=self._settings.s3_region,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            config=BotoConfig(
                signature_version="s3v4", s3={"addressing_style": "path"}
            ),
        )

    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def ensure_buckets(self) -> None:
        """Create the two buckets if they don't exist. Idempotent."""
        if self._buckets_ensured:
            return
        client = self.client()
        for bucket in (
            self._settings.s3_bucket_ingest,
            self._settings.s3_bucket_output,
        ):
            try:
                client.head_bucket(Bucket=bucket)
            except ClientError:
                try:
                    client.create_bucket(Bucket=bucket)
                    logger.info("Created bucket %s", bucket)
                except ClientError as e:
                    # If a parallel process created it first, treat as success.
                    if e.response.get("Error", {}).get("Code") not in {
                        "BucketAlreadyOwnedByYou",
                        "BucketAlreadyExists",
                    }:
                        raise StorageError(
                            f"Failed to create bucket {bucket!r}: {e}"
                        ) from e
        self._buckets_ensured = True

    # -- Operations -----------------------------------------------------------

    def upload_fileobj(
        self,
        *,
        bucket: str,
        key: str,
        data: io.BufferedIOBase | bytes,
        content_type: str | None = None,
    ) -> None:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            if isinstance(data, (bytes, bytearray)):  # noqa: UP038
                self.client().upload_fileobj(
                    io.BytesIO(bytes(data)), bucket, key, ExtraArgs=extra or None
                )
            else:
                self.client().upload_fileobj(data, bucket, key, ExtraArgs=extra or None)
        except EndpointConnectionError as e:
            raise StorageError(f"Cannot reach S3 endpoint: {e}") from e

    def download_to_file(self, *, bucket: str, key: str, dest_path: str) -> None:
        try:
            self.client().download_file(bucket, key, dest_path)
        except ClientError as e:
            raise StorageError(f"Failed to download s3://{bucket}/{key}: {e}") from e

    def head_object(self, *, bucket: str, key: str) -> dict[str, Any] | None:
        try:
            return dict(self.client().head_object(Bucket=bucket, Key=key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return None
            raise StorageError(
                f"head_object failed for s3://{bucket}/{key}: {e}"
            ) from e

    def get_object_range(self, *, bucket: str, key: str, start: int, end: int) -> bytes:
        """Fetch a byte range. Used for magic-byte validation."""
        try:
            resp = self.client().get_object(
                Bucket=bucket, Key=key, Range=f"bytes={start}-{end}"
            )
            return bytes(resp["Body"].read())
        except ClientError as e:
            raise StorageError(f"get_object range failed: {e}") from e

    def object_exists(self, *, bucket: str, key: str) -> bool:
        return self.head_object(bucket=bucket, key=key) is not None

    def generate_presigned_put(
        self, *, bucket: str, key: str, content_type: str | None = None
    ) -> tuple[str, int]:
        """Generate a presigned PUT URL. Returns (url, expires_in_seconds)."""
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        url = self.client().generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=self._settings.s3_presign_expiry_seconds,
        )
        return url, self._settings.s3_presign_expiry_seconds

    def generate_presigned_get(self, *, bucket: str, key: str) -> tuple[str, int]:
        url = self.client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=self._settings.s3_presign_expiry_seconds,
        )
        return url, self._settings.s3_presign_expiry_seconds


@lru_cache(maxsize=1)
def get_storage_service() -> S3Service:
    """Process-wide singleton."""
    return S3Service()
