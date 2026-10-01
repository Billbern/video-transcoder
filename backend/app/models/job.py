"""Job ORM model.

A `Job` represents one user request to transcode a source video into a preset.
The lifecycle is:

    UPLOADING -> QUEUED -> PROCESSING -> COMPLETED
                                     -> FAILED
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import CHAR, TypeDecorator

from app.db.base import Base


class JobStatus(str, enum.Enum):
    """Discrete states a Job can occupy."""

    UPLOADING = "UPLOADING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# --- UUID column that works on both Postgres and SQLite -----------------------------


class GUID(TypeDecorator[uuid.UUID]):
    """Platform-independent UUID column."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:  # noqa: ARG002
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(
        self, value: uuid.UUID | str | None, dialect: Any
    ) -> uuid.UUID | str | None:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))

    def process_result_value(
        self, value: Any, dialect: Any
    ) -> uuid.UUID | None:  # noqa: ARG002
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Job(Base):
    """A single transcode request, tracked through its full lifecycle."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

    # Source metadata (provided at /initiate).
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # What the user wants (e.g., "720p").
    target_preset: Mapped[str] = mapped_column(String(32), nullable=False)

    # Lifecycle.
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False, length=20),
        nullable=False,
        default=JobStatus.UPLOADING,
        index=True,
    )

    # Storage coordinates.
    ingest_object_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    output_object_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Failure diagnostics.
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ffmpeg_stderr: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job id={self.id} status={self.status.value!r} preset={self.target_preset!r}>"
