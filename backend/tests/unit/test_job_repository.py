"""Unit tests for `JobRepository` against an in-memory SQLite DB."""

from __future__ import annotations

import uuid

import pytest
from app.models.job import JobStatus
from app.services.job_repository import JobRepository


@pytest.fixture
def repo(db_session):
    return JobRepository(db_session)


@pytest.mark.asyncio
async def test_create_and_get(repo) -> None:
    job = await repo.create(
        filename="video.mp4",
        content_type="video/mp4",
        file_size=12345,
        target_preset="720p",
        ingest_object_key="jobs/abc/source.mp4",
    )
    assert job.id is not None
    assert job.status == JobStatus.UPLOADING

    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.filename == "video.mp4"


@pytest.mark.asyncio
async def test_set_status_to_processing(repo) -> None:
    job = await repo.create(
        filename="a.mov",
        content_type="video/quicktime",
        file_size=1,
        target_preset="720p",
        ingest_object_key="k",
    )
    updated = await repo.set_status(job.id, JobStatus.PROCESSING)
    assert updated is not None
    assert updated.status == JobStatus.PROCESSING


@pytest.mark.asyncio
async def test_mark_failed_persists_error(repo) -> None:
    job = await repo.create(
        filename="b.mp4",
        content_type="video/mp4",
        file_size=1,
        target_preset="720p",
        ingest_object_key="k",
    )
    await repo.mark_failed(
        job.id,
        error_code="1003",
        error_message="ffmpeg crashed",
        ffmpeg_stderr="some stderr",
    )
    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.FAILED
    assert fetched.error_code == "1003"
    assert fetched.ffmpeg_stderr == "some stderr"


@pytest.mark.asyncio
async def test_list_recent_ordering(repo) -> None:
    await repo.create(
        filename="a.mp4",
        content_type="video/mp4",
        file_size=1,
        target_preset="720p",
        ingest_object_key="k1",
    )
    b = await repo.create(
        filename="b.mp4",
        content_type="video/mp4",
        file_size=1,
        target_preset="720p",
        ingest_object_key="k2",
    )
    items = await repo.list_recent()
    assert len(items) >= 2
    # Most recent first.
    assert items[0].id == b.id


@pytest.mark.asyncio
async def test_get_missing_returns_none(repo) -> None:
    assert await repo.get(uuid.uuid4()) is None
