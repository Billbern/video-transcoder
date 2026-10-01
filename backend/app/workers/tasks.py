"""Celery tasks.

`transcode_job` is the only task in V1. It:
  1. Downloads the source object from Bucket A.
  2. Validates magic bytes.
  3. Runs FFmpeg locally.
  4. Uploads the result to Bucket B.
  5. Updates the Job row to COMPLETED (or FAILED on any exception).

The worker uses a *sync* SQLAlchemy session because Celery's prefork model
doesn't share the FastAPI event loop.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.job import Job, JobStatus
from app.services.ffmpeg_processor import FFmpegError, FFmpegProcessor
from app.services.formats import match_magic_bytes
from app.services.storage import S3Service, StorageError
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# --- Error code catalogue (mirrored on the frontend) -------------------------
class ErrorCode:
    UNSUPPORTED_FORMAT = "1001"
    CORRUPTED_SOURCE = "1002"
    FFMPEG_FAILED = "1003"
    STORAGE_QUOTA_EXCEEDED = "1004"
    STORAGE_UNAVAILABLE = "1005"
    INTERNAL = "1999"


# --- Sync DB session --------------------------------------------------------
_sync_engine = None
_sync_session_factory: sessionmaker[Session] | None = None


def _get_sync_factory() -> sessionmaker[Session]:
    global _sync_engine, _sync_session_factory
    if _sync_session_factory is None:
        url = get_settings().resolve_sync_url()
        connect_args: dict[str, object] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _sync_engine = create_engine(
            url, future=True, pool_pre_ping=True, connect_args=connect_args
        )
        _sync_session_factory = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return _sync_session_factory


def reset_sync_session() -> None:
    """Tear down the sync engine (used by the test suite to cross-loop safely)."""
    global _sync_engine, _sync_session_factory
    if _sync_engine is not None:
        _sync_engine.dispose()
    _sync_engine = None
    _sync_session_factory = None


# --- Task -------------------------------------------------------------------


@celery_app.task(  # type: ignore[misc]
    name="app.workers.tasks.transcode_job",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def transcode_job(self: Any, job_id: str) -> dict[str, Any]:
    """Execute the transcode pipeline for `job_id` (string form of UUID)."""
    settings = get_settings()
    storage = S3Service(settings)
    workdir = Path(settings.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    factory = _get_sync_factory()
    with factory() as session:
        job = session.get(Job, uuid.UUID(job_id))
        if job is None:
            logger.error("Job %s not found in worker", job_id)
            return {"job_id": job_id, "status": "missing"}

        job.status = JobStatus.PROCESSING
        session.commit()

        input_path = workdir / f"{job.id}-input"
        output_path = workdir / f"{job.id}-output.mp4"

        try:
            # 1. Download source.
            storage.download_to_file(
                bucket=settings.s3_bucket_ingest,
                key=job.ingest_object_key,
                dest_path=str(input_path),
            )

            # 2. Magic-byte validation.
            with input_path.open("rb") as f:
                head = f.read(32)
            container = match_magic_bytes(head)
            if container is None:
                raise FFmpegError(
                    returncode=-1,
                    stderr=f"Unrecognized video format (magic bytes: {head[:8]!r})",
                )

            # 3. Transcode.
            processor = FFmpegProcessor(preset=job.target_preset)
            # `transcode` is async; run it inline via asyncio.
            import asyncio

            asyncio.run(processor.transcode(input_path, output_path))

            # 4. Upload output to Bucket B.
            output_key = f"{job.id}/output.mp4"
            with output_path.open("rb") as f:
                storage.upload_fileobj(
                    bucket=settings.s3_bucket_output,
                    key=output_key,
                    data=f,
                    content_type="video/mp4",
                )

            # 5. Mark COMPLETED.
            job.status = JobStatus.COMPLETED
            job.output_object_key = output_key
            job.completed_at = datetime.now(tz=UTC)
            session.commit()

            logger.info("Job %s completed", job_id)
            return {"job_id": job_id, "status": "completed", "output_key": output_key}

        except FFmpegError as e:
            job.status = JobStatus.FAILED
            job.error_code = ErrorCode.FFMPEG_FAILED
            job.error_message = "Corrupted or unsupported source file."
            job.ffmpeg_stderr = e.stderr[:8000]
            session.commit()
            logger.warning("Job %s failed (ffmpeg): %s", job_id, e)
            return {"job_id": job_id, "status": "failed", "code": job.error_code}

        except StorageError as e:
            job.status = JobStatus.FAILED
            job.error_code = ErrorCode.STORAGE_UNAVAILABLE
            job.error_message = "Storage backend unavailable."
            session.commit()
            logger.error("Job %s storage error: %s", job_id, e)
            return {"job_id": job_id, "status": "failed", "code": job.error_code}

        except (
            Exception
        ) as e:  # noqa: BLE001 - last-resort catch per PRD resilience req
            job.status = JobStatus.FAILED
            job.error_code = ErrorCode.INTERNAL
            job.error_message = str(e)[:500]
            session.commit()
            logger.exception("Job %s crashed", job_id)
            return {"job_id": job_id, "status": "failed", "code": job.error_code}

        finally:
            # Best-effort cleanup of local artifacts.
            for p in (input_path, output_path):
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
