"""Celery tasks.

`transcode_job` is the only task in V1. It:
  1. Cheap-preflight magic-byte check via ranged GET against Bucket A.
  2. Downloads the source object from Bucket A.
  3. Runs FFmpeg locally (stderr captured to disk + DB preview).
  4. Uploads the result to Bucket B.
  5. Updates the Job row to COMPLETED (or FAILED on any exception).

Resilience contract (per `docs/prd.md`):
  - A failed FFmpeg run marks the job FAILED and is *not* retried (transient
    ffmpeg issues are extremely rare; we don't want to burn CPU on a
    corrupted source).
  - A transient S3 failure (network blip) is retried with exponential
    backoff up to `max_retries` times; only then is the job FAILED with
    `STORAGE_UNAVAILABLE`.
  - All exceptions are caught; the worker never crashes.

The worker uses a *sync* SQLAlchemy session because Celery's prefork model
doesn't share the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.job import Job, JobStatus
from app.services.ffmpeg_processor import FFmpegError, FFmpegProcessor
from app.services.formats import match_magic_bytes
from app.services.storage import S3Service, StorageError
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# How many bytes we need to read for magic-byte detection. The longest
# signature is 4 bytes at offset 4, so 32 bytes is plenty.
MAGIC_BYTES_PROBE_SIZE = 32

# Cap for the stderr preview stored on the Job row.
FFMPEG_STDERR_PREVIEW_BYTES = 2000


# --- Error code catalogue (mirrored on the frontend) -------------------------
class ErrorCode:
    UNSUPPORTED_FORMAT = "1001"
    CORRUPTED_SOURCE = "1002"
    FFMPEG_FAILED = "1003"
    STORAGE_QUOTA_EXCEEDED = "1004"
    STORAGE_UNAVAILABLE = "1005"
    INTERNAL = "1999"


# --- Custom exceptions for transient vs permanent classification -------------


class FormatError(RuntimeError):
    """Raised when the source file fails magic-byte validation.

    Permanent: not worth retrying; the source is what it is.
    """


class TransientError(RuntimeError):
    """Marker for errors that should trigger a Celery auto-retry.

    The `retry_after` attribute hints at the backoff (seconds).
    """

    def __init__(self, message: str, *, retry_after: int = 30) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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


# --- Helpers ----------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync code.

    Celery worker threads don't have a running event loop, so we open one
    per task invocation. This is fine because each worker is single-threaded.
    """
    return asyncio.run(coro)


def _stderr_log_path(workdir: Path, job_id: uuid.UUID) -> Path:
    return workdir / "ffmpeg-logs" / f"{job_id}.log"


def _write_stderr_log(workdir: Path, job_id: uuid.UUID, stderr: str) -> Path:
    """Write the full stderr to disk; return the path."""
    path = _stderr_log_path(workdir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate the file (one transcode per job id).
    path.write_text(stderr, encoding="utf-8", errors="replace")
    return path


def _mark_failed(
    job: Job,
    *,
    error_code: str,
    error_message: str,
    ffmpeg_stderr: str | None = None,
    log_path: Path | None = None,
) -> None:
    """Transition a Job to FAILED with full diagnostics."""
    job.status = JobStatus.FAILED
    job.error_code = error_code
    job.error_message = error_message
    if ffmpeg_stderr is not None:
        # Keep the preview column small; full log is on disk.
        job.ffmpeg_stderr = ffmpeg_stderr[:FFMPEG_STDERR_PREVIEW_BYTES]
    # We don't have a column for log path; encode it into ffmpeg_stderr as a
    # header so operators can find it. (Sprint 3 will introduce a dedicated
    # column if it becomes a frequent request.)
    if log_path is not None:
        job.ffmpeg_stderr = f"[full log: {log_path}]\n" + (job.ffmpeg_stderr or "")


# --- Task -------------------------------------------------------------------


@celery_app.task(  # type: ignore[misc]
    name="app.workers.tasks.transcode_job",
    bind=True,
    max_retries=3,
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

        # Move to PROCESSING as soon as the worker picks it up. Anything that
        # fails before we mark FAILED will be retried; on the final retry the
        # task body itself marks FAILED.
        job.status = JobStatus.PROCESSING
        session.commit()

        input_path = workdir / f"{job.id}-input"
        output_path = workdir / f"{job.id}-output.mp4"

        try:
            # 1. Cheap preflight: ranged GET for magic bytes (PRD §1.4).
            try:
                head = storage.get_object_range(
                    bucket=settings.s3_bucket_ingest,
                    key=job.ingest_object_key,
                    start=0,
                    end=MAGIC_BYTES_PROBE_SIZE - 1,
                )
            except StorageError as e:
                # Storage blip -> transient; retry with backoff.
                raise TransientError(
                    f"magic-byte probe failed: {e}", retry_after=15
                ) from e

            container = match_magic_bytes(head)
            if container is None:
                raise FormatError(
                    f"Unrecognized video format (magic bytes: {head[:8]!r})"
                )
            logger.debug(
                "Job %s: detected container %s via magic bytes",
                job_id,
                container,
            )

            # 2. Download the full source.
            try:
                storage.download_to_file(
                    bucket=settings.s3_bucket_ingest,
                    key=job.ingest_object_key,
                    dest_path=str(input_path),
                )
            except StorageError as e:
                raise TransientError(
                    f"source download failed: {e}", retry_after=30
                ) from e

            # 3. Run FFmpeg.
            try:
                processor = FFmpegProcessor(preset=job.target_preset)
                _run_async(processor.transcode(input_path, output_path))
            except FFmpegError as e:
                # Permanent: log full stderr to disk + preview to DB.
                log_path = _write_stderr_log(workdir, job.id, e.stderr)
                _mark_failed(
                    job,
                    error_code=ErrorCode.FFMPEG_FAILED,
                    error_message="FFmpeg could not process the source file.",
                    ffmpeg_stderr=e.stderr,
                    log_path=log_path,
                )
                session.commit()
                logger.warning(
                    "Job %s FAILED (ffmpeg rc=%s): %s",
                    job_id,
                    e.returncode,
                    e.stderr[:200],
                )
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "code": ErrorCode.FFMPEG_FAILED,
                }

            # 4. Upload the result to Bucket B.
            output_key = f"{job.id}/output.mp4"
            try:
                with output_path.open("rb") as f:
                    storage.upload_fileobj(
                        bucket=settings.s3_bucket_output,
                        key=output_key,
                        data=f,
                        content_type="video/mp4",
                    )
            except StorageError as e:
                raise TransientError(
                    f"output upload failed: {e}", retry_after=30
                ) from e

            # 5. Mark COMPLETED.
            job.status = JobStatus.COMPLETED
            job.output_object_key = output_key
            job.completed_at = datetime.now(tz=UTC)
            session.commit()

            logger.info("Job %s completed", job_id)
            return {
                "job_id": job_id,
                "status": "completed",
                "output_key": output_key,
            }

        except FormatError as e:
            _mark_failed(
                job,
                error_code=ErrorCode.UNSUPPORTED_FORMAT,
                error_message=str(e),
            )
            session.commit()
            logger.warning("Job %s FAILED (format): %s", job_id, e)
            return {
                "job_id": job_id,
                "status": "failed",
                "code": ErrorCode.UNSUPPORTED_FORMAT,
            }

        except TransientError as e:
            # Honor max_retries; Celery will raise Retry on the final attempt.
            logger.warning(
                "Job %s transient error (attempt %s/%s): %s",
                job_id,
                self.request.retries + 1,
                self.max_retries,
                e,
            )
            try:
                raise self.retry(exc=e, countdown=e.retry_after)
            except MaxRetriesExceeded:
                # self.retry raises Retry, which Celery treats as "retry".
                # If max_retries was hit, retry() raises MaxRetriesExceeded.
                _mark_failed(
                    job,
                    error_code=ErrorCode.STORAGE_UNAVAILABLE,
                    error_message=f"Storage unavailable after retries: {e}",
                )
                session.commit()
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "code": ErrorCode.STORAGE_UNAVAILABLE,
                }

        except OperationalError as e:
            # DB is down: treat as transient. The worker process will likely
            # crash and be restarted by the orchestrator.
            logger.exception("Job %s DB error: %s", job_id, e)
            try:
                raise self.retry(exc=e, countdown=10)
            except MaxRetriesExceeded:
                _mark_failed(
                    job,
                    error_code=ErrorCode.INTERNAL,
                    error_message="Database unavailable after retries.",
                )
                session.commit()
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "code": ErrorCode.INTERNAL,
                }

        except Exception as e:  # noqa: BLE001 - last-resort per PRD resilience
            # If `self.retry()` raised `Retry` (or its `MaxRetriesExceededError`
            # variant slipped past the catches above), re-raise it so Celery
            # can schedule a retry. The PRD's resilience requirement is that
            # the worker never crashes — but Retry is the *correct* control
            # flow, not a crash.
            try:
                from celery.exceptions import (
                    MaxRetriesExceededError,
                    Retry,
                )

                if isinstance(e, (Retry, MaxRetriesExceededError)):
                    raise
            except ImportError:  # pragma: no cover
                pass

            _mark_failed(
                job,
                error_code=ErrorCode.INTERNAL,
                error_message=str(e)[:500],
            )
            session.commit()
            logger.exception("Job %s crashed", job_id)
            return {
                "job_id": job_id,
                "status": "failed",
                "code": ErrorCode.INTERNAL,
            }

        finally:
            # Best-effort cleanup of local input/output (logs are kept).
            for p in (input_path, output_path):
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass


# Celery's self.retry raises celery.exceptions.Retry on success (Celery catches
# it). When max_retries is reached, it raises MaxRetriesExceededError. Importing
# here so type-checkers don't complain.
try:
    from celery.exceptions import (
        MaxRetriesExceededError as MaxRetriesExceeded,
    )
    from celery.exceptions import (
        Retry,
    )
except Exception:  # pragma: no cover - celery always installed at runtime
    MaxRetriesExceeded = Exception
    Retry = Exception
