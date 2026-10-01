"""Database handler for the `Job` entity.

Encapsulates all reads/writes against the `jobs` table so the API + worker
layers never touch SQLAlchemy session state directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus


class JobRepository:
    """Thin async repository over the `Job` ORM model.

    A repository instance is bound to a single session; create a fresh one per
    request/task.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Commands -------------------------------------------------------------

    async def create(
        self,
        *,
        filename: str,
        content_type: str,
        file_size: int,
        target_preset: str,
        ingest_object_key: str,
        status: JobStatus = JobStatus.UPLOADING,
    ) -> Job:
        """Insert a new Job and return the populated ORM object."""
        job = Job(
            id=uuid.uuid4(),
            filename=filename,
            content_type=content_type,
            file_size=file_size,
            target_preset=target_preset,
            ingest_object_key=ingest_object_key,
            status=status,
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def set_status(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        ffmpeg_stderr: str | None = None,
        output_object_key: str | None = None,
    ) -> Job | None:
        """Transition a Job to `status` and optionally persist failure metadata.

        Returns the refreshed row, or `None` if the job was not found.
        """
        job = await self.get(job_id)
        if job is None:
            return None

        job.status = status
        if error_code is not None:
            job.error_code = error_code
        if error_message is not None:
            job.error_message = error_message
        if ffmpeg_stderr is not None:
            job.ffmpeg_stderr = ffmpeg_stderr
        if output_object_key is not None:
            job.output_object_key = output_object_key

        await self._session.flush()
        return job

    async def mark_failed(
        self,
        job_id: uuid.UUID,
        *,
        error_code: str,
        error_message: str,
        ffmpeg_stderr: str | None = None,
    ) -> Job | None:
        """Shortcut for `set_status(FAILED)` with error metadata in one call."""
        return await self.set_status(
            job_id,
            JobStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            ffmpeg_stderr=ffmpeg_stderr,
        )

    # -- Queries --------------------------------------------------------------

    async def get(self, job_id: uuid.UUID) -> Job | None:
        return await self._session.get(Job, job_id)

    async def list_recent(self, *, limit: int = 50) -> Sequence[Job]:
        """Return the most recent jobs (newest first)."""
        stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_status(self, statuses: Iterable[JobStatus]) -> Sequence[Job]:
        stmt = (
            select(Job)
            .where(Job.status.in_(tuple(statuses)))
            .order_by(Job.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Job))
        return int(result.scalar_one())
