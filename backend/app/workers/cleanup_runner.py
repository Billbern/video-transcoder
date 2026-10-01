"""Server-side cleanup of zombie uploads.

Per `docs/implementation.md` (Sprint 2) we periodically delete `UPLOADING` jobs
older than 1 hour and their underlying ingest objects. This module exposes a
single `run_once()` entrypoint suitable for invocation from a cron sidecar.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models.job import Job, JobStatus
from app.services.storage import S3Service, StorageError

logger = logging.getLogger(__name__)

ZOMBIE_AGE = timedelta(hours=1)


def run_once() -> int:
    """Delete zombie uploads; returns number of rows cleaned."""
    settings = get_settings()
    engine = create_engine(settings.resolve_sync_url(), future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    storage = S3Service(settings)

    cleaned = 0
    cutoff = datetime.now(tz=UTC) - ZOMBIE_AGE
    with Session() as session:
        zombies = (
            session.query(Job)
            .filter(Job.status == JobStatus.UPLOADING, Job.created_at < cutoff)
            .all()
        )
        for job in zombies:
            try:
                storage.client().delete_object(
                    Bucket=settings.s3_bucket_ingest, Key=job.ingest_object_key
                )
            except StorageError as e:
                logger.warning(
                    "Failed to delete zombie object %s: %s", job.ingest_object_key, e
                )
            session.delete(job)
            cleaned += 1
        session.commit()

    if cleaned:
        logger.info("Cleanup removed %d zombie job(s)", cleaned)
    return cleaned


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    run_once()
