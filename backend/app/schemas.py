"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.job import JobStatus

Preset = Literal["720p"]  # V1 ships with one preset per docs/prd.md
ALLOWED_PRESETS: tuple[str, ...] = ("720p",)


# --- Requests -------------------------------------------------------------


class InitiateRequest(BaseModel):
    """Body for `POST /api/initiate`.

    The frontend calls this first to register the file before requesting a
    presigned URL from S3.
    """

    filename: str = Field(..., min_length=1, max_length=500)
    content_type: str = Field(..., min_length=1, max_length=100)
    file_size: int = Field(..., gt=0, le=2 * 1024 * 1024 * 1024)
    preset: str = Field(..., description="Target transcoding preset, e.g., '720p'.")

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, value: str) -> str:
        if value not in ALLOWED_PRESETS:
            raise ValueError(
                f"Unsupported preset {value!r}; allowed: {list(ALLOWED_PRESETS)}"
            )
        return value


class TranscodeRequest(BaseModel):
    """Body for `POST /api/transcode`.

    The frontend calls this after the direct-to-S3 upload has completed.
    """

    job_id: uuid.UUID
    preset: str = Field("720p")

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, value: str) -> str:
        if value not in ALLOWED_PRESETS:
            raise ValueError(
                f"Unsupported preset {value!r}; allowed: {list(ALLOWED_PRESETS)}"
            )
        return value


# --- Responses ------------------------------------------------------------


class InitiateResponse(BaseModel):
    """Response from `POST /api/initiate`."""

    job_id: uuid.UUID
    upload_url: str = Field(..., description="Presigned PUT URL for the raw object.")
    ingest_object_key: str
    expires_in: int = Field(..., description="Seconds until the presigned URL expires.")


class JobResponse(BaseModel):
    """Response from `POST /api/transcode` and `GET /api/jobs/{id}`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    target_preset: str
    status: JobStatus
    error_code: str | None = None
    error_message: str | None = None
    download_url: str | None = None
    download_url_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class JobListResponse(BaseModel):
    """Response from `GET /api/jobs`."""

    items: list[JobResponse]
    total: int
