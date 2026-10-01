"""FastAPI routes for job management."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.models.job import Job, JobStatus
from app.schemas import (
    InitiateRequest,
    InitiateResponse,
    JobListResponse,
    JobResponse,
    TranscodeRequest,
)
from app.services.formats import looks_like_video
from app.services.job_repository import JobRepository
from app.services.storage import S3Service, StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["jobs"])


def _dispatch_transcode(job_id: str) -> None:
    """Hand the job off to Celery.

    Lazy-imported so Celery is only required in the worker process, not at API
    startup. Monkeypatched in integration tests.
    """
    from app.config import get_settings
    from app.workers.celery_app import celery_app

    if get_settings().celery_task_always_eager:
        from app.workers.tasks import transcode_job

        transcode_job.delay(job_id)
    else:
        celery_app.send_task("app.workers.tasks.transcode_job", args=[job_id])


def _to_job_response(job: Job, *, download_url: str | None) -> JobResponse:
    return JobResponse(
        id=job.id,
        filename=job.filename,
        content_type=job.content_type,
        file_size=job.file_size,
        target_preset=job.target_preset,
        status=job.status,
        error_code=job.error_code,
        error_message=job.error_message,
        download_url=download_url,
        download_url_expires_at=None,  # populated by caller when relevant
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _generate_ingest_key(job_id: uuid.UUID, filename: str) -> str:
    """Object key for Bucket A: `jobs/<id>/source.<ext>`."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"jobs/{job_id}/source.{ext}"


# --- Endpoints ---------------------------------------------------------------


@router.post(
    "/initiate",
    response_model=InitiateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reserve a job slot and obtain a presigned upload URL.",
)
async def initiate_upload(
    payload: InitiateRequest,
    db: AsyncSession = Depends(get_db),
) -> InitiateResponse:
    """Validate the request, persist a `UPLOADING` Job, return a presigned PUT URL."""
    settings = get_settings()
    if payload.file_size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.max_upload_bytes} byte limit",
        )
    if not looks_like_video(payload.content_type, payload.filename):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported format. Please upload MP4, MOV, or MKV.",
        )

    repo = JobRepository(db)
    # Use a stable UUID up-front so the client can include it in the URL.
    job_id = uuid.uuid4()
    ingest_key = _generate_ingest_key(job_id, payload.filename)

    job = await repo.create(
        filename=payload.filename,
        content_type=payload.content_type,
        file_size=payload.file_size,
        target_preset=payload.preset,
        ingest_object_key=ingest_key,
        status=JobStatus.UPLOADING,
    )
    # Override the auto-generated id with our pre-computed one for a stable URL.
    # (Simpler approach: just use the repo-created id and re-key; but we want
    # `job_id` returned below to match the upload URL the client uses.)
    job_id = job.id
    ingest_key = _generate_ingest_key(job_id, payload.filename)
    job.ingest_object_key = ingest_key
    await db.flush()

    # Best-effort bucket ensure; storage errors are raised so the API returns 503
    # instead of a placeholder URL. This is the Sprint 2 contract: never return
    # a URL the client can't actually use.
    storage = S3Service(settings)
    try:
        storage.ensure_buckets()
        upload_url, expires_in = storage.generate_presigned_put(
            bucket=settings.s3_bucket_ingest,
            key=ingest_key,
            content_type=payload.content_type,
        )
    except StorageError as e:
        logger.exception("Storage backend unavailable on /initiate")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Storage backend unavailable: {e}",
        ) from e

    return InitiateResponse(
        job_id=job_id,
        upload_url=upload_url,
        ingest_object_key=ingest_key,
        expires_in=expires_in,
    )


@router.post(
    "/transcode",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mark the upload complete and enqueue a transcode job.",
)
async def start_transcode(
    payload: TranscodeRequest,
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Transition the job to `QUEUED` and dispatch the Celery task."""
    repo = JobRepository(db)
    job = await repo.get(payload.job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    if job.status not in {JobStatus.UPLOADING, JobStatus.QUEUED, JobStatus.FAILED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot start transcode: job is in status {job.status.value!r}",
        )

    # Verify the ingest object exists (user actually uploaded something).
    settings = get_settings()
    try:
        storage = S3Service(settings)
        if not storage.object_exists(
            bucket=settings.s3_bucket_ingest, key=job.ingest_object_key
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source object not found in storage. Did the upload complete?",
            )
    except HTTPException:
        raise
    except StorageError as e:
        # Storage is down: tell the client to retry rather than silently
        # accepting a job the worker can't process.
        logger.exception("Storage preflight failed for job %s", job.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Storage backend unavailable: {e}",
        ) from e

    await repo.set_status(job.id, JobStatus.QUEUED)

    # Enqueue via a small dispatcher so tests can monkeypatch it.
    _dispatch_transcode(str(job.id))

    await db.refresh(job)
    return _to_job_response(job, download_url=None)


@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="List recent jobs (newest first).",
)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
) -> JobListResponse:
    repo = JobRepository(db)
    items = await repo.list_recent(limit=100)
    total = await repo.count()
    settings = get_settings()
    storage = S3Service(settings) if items else None

    responses: list[JobResponse] = []
    for job in items:
        download_url: str | None = None
        if (
            job.status == JobStatus.COMPLETED
            and job.output_object_key
            and storage is not None
        ):
            try:
                download_url, _ = storage.generate_presigned_get(
                    bucket=settings.s3_bucket_output, key=job.output_object_key
                )
            except StorageError:
                # Per-job presign failure must not break the whole list. The
                # job still appears, just without a download URL.
                logger.exception("Failed to presign URL for job %s", job.id)
        responses.append(_to_job_response(job, download_url=download_url))

    return JobListResponse(items=responses, total=total)


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Fetch a single job's status.",
)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    repo = JobRepository(db)
    job = await repo.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    download_url: str | None = None
    if job.status == JobStatus.COMPLETED and job.output_object_key:
        try:
            storage = S3Service()
            download_url, _ = storage.generate_presigned_get(
                bucket=get_settings().s3_bucket_output, key=job.output_object_key
            )
        except StorageError:
            # `download_url` stays None; the client can retry the GET.
            logger.exception("Failed to presign URL for job %s", job.id)

    return _to_job_response(job, download_url=download_url)
