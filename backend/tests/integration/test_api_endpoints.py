"""Integration tests against the FastAPI app + in-memory SQLite.

Per `docs/testing_strategies.md` these verify that API calls exercise the
repository and (in eager Celery mode) the worker pipeline.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_health(client) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_initiate_rejects_bad_format(client) -> None:
    r = await client.post(
        "/api/initiate",
        json={
            "filename": "evil.exe",
            "content_type": "application/octet-stream",
            "file_size": 1024,
            "preset": "720p",
        },
    )
    assert r.status_code == 415
    assert "Unsupported format" in r.json()["detail"]


@pytest.mark.asyncio
async def test_initiate_rejects_oversize(client) -> None:
    """Oversize files are rejected by Pydantic field validation (422)."""
    r = await client.post(
        "/api/initiate",
        json={
            "filename": "big.mp4",
            "content_type": "video/mp4",
            "file_size": 10 * 1024 * 1024 * 1024,  # 10 GB
            "preset": "720p",
        },
    )
    assert r.status_code == 422
    assert "file_size" in r.text


@pytest.mark.asyncio
async def test_initiate_rejects_size_above_runtime_limit(client) -> None:
    """Sizes within Pydantic's max but above the runtime cap yield 413.

    We test this by lowering the runtime cap via env override before the app is
    instantiated. The default Pydantic ceiling is 2 GiB; we patch the settings
    by mutating the cached instance.
    """
    from app.config import get_settings

    settings = get_settings()
    original = settings.max_upload_bytes
    settings.max_upload_bytes = 1024  # 1 KiB
    try:
        r = await client.post(
            "/api/initiate",
            json={
                "filename": "medium.mp4",
                "content_type": "video/mp4",
                "file_size": 4096,
                "preset": "720p",
            },
        )
        assert r.status_code == 413
    finally:
        settings.max_upload_bytes = original


@pytest.mark.asyncio
async def test_initiate_succeeds_with_placeholder_storage(client) -> None:
    # S3 is not running in tests; the API should fall back to a placeholder URL.
    r = await client.post(
        "/api/initiate",
        json={
            "filename": "clip.mp4",
            "content_type": "video/mp4",
            "file_size": 1024 * 1024,
            "preset": "720p",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "job_id" in body
    assert "upload_url" in body
    assert body["expires_in"] > 0


@pytest.mark.asyncio
async def test_transcode_unknown_job_returns_404(client) -> None:
    import uuid as _u

    r = await client.post(
        "/api/transcode",
        json={"job_id": str(_u.uuid4()), "preset": "720p"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_full_flow_with_mocked_storage(client, monkeypatch) -> None:
    """End-to-end-ish flow with S3 + worker both mocked.

    We patch the Celery dispatch entrypoint to a no-op so the test doesn't
    depend on a live worker or a shared SQLite DB across event loops.
    """
    from app.models.job import JobStatus

    sent: list[str] = []

    def fake_dispatch(job_id: str) -> None:
        sent.append(job_id)

    monkeypatch.setattr("app.api.jobs._dispatch_transcode", fake_dispatch)

    # 1. Initiate.
    init = await client.post(
        "/api/initiate",
        json={
            "filename": "demo.mp4",
            "content_type": "video/mp4",
            "file_size": 4096,
            "preset": "720p",
        },
    )
    assert init.status_code == 201, init.text
    job_id = init.json()["job_id"]

    # 2. Tell the job we have uploaded the file (mock S3 head).
    with (
        patch("app.services.storage.S3Service.object_exists", return_value=True),
        patch("app.services.storage.S3Service.ensure_buckets"),
    ):
        r = await client.post(
            "/api/transcode",
            json={"job_id": job_id, "preset": "720p"},
        )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] in {
        JobStatus.QUEUED.value,
        JobStatus.PROCESSING.value,
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
    }

    # The dispatch was invoked with the right job id.
    assert sent == [job_id]

    # 3. Fetch it back.
    with patch("app.services.storage.S3Service.ensure_buckets"):
        r = await client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200, r.text
    fetched = r.json()
    assert fetched["id"] == job_id


@pytest.mark.asyncio
async def test_list_jobs(client) -> None:
    r = await client.get("/api/jobs")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
