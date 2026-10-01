"""Server-side cleanup of zombie uploads.

Per `docs/implementation.md` (Sprint 2) we periodically delete `UPLOADING` jobs
older than 1 hour and their underlying ingest objects. This module exposes:

  - `run_once()` — a synchronous entrypoint for a cron sidecar.
  - `cleanup_zombies_task` — a Celery task registered on the shared app, with
    a 15-minute Beat schedule declared in `celery_app.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models.job import Job, JobStatus
from app.services.storage import S3Service, StorageError

logger = logging.getLogger(__name__)

ZOMBIE_AGE: Final[timedelta] = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """Outcome of one cleanup run.

    The fields are deliberately flat so a metrics sidecar (Prometheus) can
    surface them without parsing logs.
    """

    zombies_found: int
    objects_deleted: int
    objects_failed: int

    @property
    def jobs_deleted(self) -> int:
        return self.zombies_found


def run_once(*, age: timedelta = ZOMBIE_AGE) -> CleanupReport:
    """Delete zombie uploads older than `age`.

    Args:
        age: A `UPLOADING` row older than this is considered a zombie.

    Returns:
        A `CleanupReport` with the number of zombies found, ingest objects
        successfully deleted, and ingest objects whose deletion failed.
    """
    settings = get_settings()
    engine = create_engine(settings.resolve_sync_url(), future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    storage = S3Service(settings)

    found = 0
    deleted = 0
    failed = 0
    cutoff = datetime.now(tz=UTC) - age

    with Session() as session:
        zombies = (
            session.query(Job)
            .filter(Job.status == JobStatus.UPLOADING, Job.created_at < cutoff)
            .all()
        )
        found = len(zombies)

        for job in zombies:
            try:
                storage.client().delete_object(
                    Bucket=settings.s3_bucket_ingest, Key=job.ingest_object_key
                )
                deleted += 1
            except StorageError as e:
                logger.warning(
                    "Failed to delete zombie object %s: %s",
                    job.ingest_object_key,
                    e,
                )
                failed += 1
            # Whether or not the S3 delete succeeded, drop the DB row: leaving
            # it as a zombie forever would block the user from re-uploading
            # with the same key. S3 will have to be reconciled out-of-band.
            session.delete(job)

        session.commit()

    report = CleanupReport(
        zombies_found=found, objects_deleted=deleted, objects_failed=failed
    )
    if found:
        logger.info(
            "Cleanup removed %d zombie job(s) (%d objects deleted, %d failed)",
            found,
            deleted,
            failed,
        )
    return report


# --- Celery task variant --------------------------------------------------


def cleanup_zombies_task() -> dict[str, int]:
    """Celery entrypoint that runs `run_once()`.

    Returns a plain dict so the result backend (Redis) can serialize it.
    """
    report = run_once()
    return {
        "zombies_found": report.zombies_found,
        "objects_deleted": report.objects_deleted,
        "objects_failed": report.objects_failed,
    }


# Register with the Celery app at import time. Doing it here (rather than in
# celery_app.py) keeps the schedule configuration co-located with the task body.
try:
    from celery.schedules import schedule

    from app.workers.celery_app import celery_app

    # 15-minute cadence. Tunable via env later if needed.
    celery_app.conf.beat_schedule = {
        "cleanup-zombie-uploads": {
            "task": "app.workers.cleanup_runner.cleanup_zombies_task",
            "schedule": schedule(run_every=15 * 60),  # seconds
        },
    }
except Exception:  # pragma: no cover - celery missing in some test envs
    pass


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    print(run_once())
