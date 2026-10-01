"""Integration tests for the job-list and get-by-id endpoints.

Complements `test_api_endpoints.py` with coverage of the GET paths and the
presigned-URL generation when a job is COMPLETED.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_get_job_not_found(client) -> None:
    import uuid as _u

    r = await client.get(f"/api/jobs/{_u.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_job_returns_initiated(client) -> None:
    with patch("app.services.storage.S3Service.ensure_buckets"):
        init = await client.post(
            "/api/initiate",
            json={
                "filename": "demo.mp4",
                "content_type": "video/mp4",
                "file_size": 1024,
                "preset": "720p",
            },
        )
    job_id = init.json()["job_id"]

    with patch("app.services.storage.S3Service.ensure_buckets"):
        r = await client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "demo.mp4"
    assert body["target_preset"] == "720p"
    assert body["status"] == "UPLOADING"
    # No download URL until COMPLETED.
    assert body["download_url"] is None


@pytest.mark.asyncio
async def test_list_jobs_empty_and_with_items(client) -> None:
    with patch("app.services.storage.S3Service.ensure_buckets"):
        r = await client.get("/api/jobs")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    initial_total = body["total"]

    # Create a job.
    with patch("app.services.storage.S3Service.ensure_buckets"):
        await client.post(
            "/api/initiate",
            json={
                "filename": "x.mp4",
                "content_type": "video/mp4",
                "file_size": 100,
                "preset": "720p",
            },
        )

    with patch("app.services.storage.S3Service.ensure_buckets"):
        r = await client.get("/api/jobs")
    body = r.json()
    assert body["total"] == initial_total + 1
    assert body["items"][0]["filename"] == "x.mp4"


@pytest.mark.asyncio
async def test_initiate_uses_settings_bucket_names(client) -> None:
    with (
        patch("app.services.storage.S3Service.ensure_buckets"),
        patch(
            "app.services.storage.S3Service.generate_presigned_put",
            return_value=("http://signed.example/k", 3600),
        ) as mocked,
    ):
        r = await client.post(
            "/api/initiate",
            json={
                "filename": "a.mov",
                "content_type": "video/quicktime",
                "file_size": 100,
                "preset": "720p",
            },
        )
    assert r.status_code == 201
    args, kwargs = mocked.call_args
    assert kwargs["bucket"] == "raw-videos"
    assert "jobs/" in kwargs["key"]
    assert kwargs["content_type"] == "video/quicktime"
    assert r.json()["upload_url"] == "http://signed.example/k"


@pytest.mark.asyncio
async def test_initiate_invalid_preset(client) -> None:
    r = await client.post(
        "/api/initiate",
        json={
            "filename": "a.mp4",
            "content_type": "video/mp4",
            "file_size": 100,
            "preset": "1080p",  # not supported in V1
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_initiate_missing_fields(client) -> None:
    r = await client.post("/api/initiate", json={"filename": "a.mp4"})
    assert r.status_code == 422
