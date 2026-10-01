"""End-to-end integration test using real Postgres + Redis containers.

Per `docs/testing_strategies.md`:

    Verify that an API call to /transcode actually creates a record in the DB
    and pushes a message to Redis.

This file is the Testcontainers-backed counterpart to the in-process
SQLite + eager-Celery tests in `test_api_endpoints.py`. It exercises the full
production code path against real infrastructure.

The module is skipped automatically when Docker isn't available (see
`testcontainers_fixtures.docker_available`).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .testcontainers_fixtures import (
    docker_available,
)


@docker_available
@pytest.mark.asyncio
async def test_initiate_writes_real_row(
    app_under_test,
    postgres_container: str,
) -> None:
    """`/api/initiate` persists a Job row in the real Postgres container."""
    # Override the CORS origin so the test request doesn't 400 in middleware.
    # Apply patches *inside* the test so the fixture rebuilds first.
    with (
        patch("app.services.storage.S3Service.ensure_buckets"),
        patch(
            "app.services.storage.S3Service.generate_presigned_put",
            return_value=("http://signed.example/k", 3600),
        ),
    ):
        transport = ASGITransport(app=app_under_test)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            r = await ac.post(
                "/api/initiate",
                json={
                    "filename": "real-postgres.mp4",
                    "content_type": "video/mp4",
                    "file_size": 2048,
                    "preset": "720p",
                },
            )

        assert r.status_code == 201, r.text
        job_id = r.json()["job_id"]

        # Verify the row landed in the real Postgres.
        eng = create_async_engine(postgres_container)
        try:
            async with eng.connect() as conn:
                result = await conn.execute(
                    text("SELECT status, filename FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
                row = result.fetchone()
        finally:
            await eng.dispose()

        assert row is not None
        assert row[0] == "UPLOADING"
        assert row[1] == "real-postgres.mp4"


@docker_available
@pytest.mark.asyncio
async def test_transcode_pushes_to_redis_broker(
    app_under_test,
    postgres_container: str,
    redis_container: str,
) -> None:
    """`/api/transcode` enqueues a real Celery task in Redis."""
    # Replace _dispatch_transcode with a real Celery send_task to exercise
    # the broker path, while keeping S3 mocked.
    from app.api import jobs as jobs_api

    def real_dispatch(job_id: str) -> None:
        from app.workers.celery_app import celery_app

        celery_app.send_task("app.workers.tasks.transcode_job", args=[job_id])

    with (
        patch.object(jobs_api, "_dispatch_transcode", new=real_dispatch),
        patch("app.services.storage.S3Service.ensure_buckets"),
        patch(
            "app.services.storage.S3Service.generate_presigned_put",
            return_value=("http://signed.example/k", 3600),
        ),
        patch("app.services.storage.S3Service.object_exists", return_value=True),
    ):
        transport = ASGITransport(app=app_under_test)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            init = await ac.post(
                "/api/initiate",
                json={
                    "filename": "queue.mp4",
                    "content_type": "video/mp4",
                    "file_size": 1024,
                    "preset": "720p",
                },
            )
            assert init.status_code == 201
            job_id = init.json()["job_id"]

            r = await ac.post(
                "/api/transcode", json={"job_id": job_id, "preset": "720p"}
            )
            assert r.status_code == 202

    # Verify the message is sitting in the Redis queue.
    import redis

    client = redis.Redis.from_url(redis_container)
    try:
        # Celery's default queue is "celery".
        queue_len = client.llen("celery")
        assert queue_len >= 1, "expected a message in the celery queue"
    finally:
        client.close()
