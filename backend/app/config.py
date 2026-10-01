"""Application configuration via pydantic-settings.

Reads from environment variables and an optional `.env` file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://vt:vt@postgres:5432/videotranscoder",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )
    sync_database_url: str = Field(
        default="postgresql+psycopg2://vt:vt@postgres:5432/videotranscoder",
        description="Sync DSN, used by Celery (which has no async loop).",
    )
    use_sqlite: bool = Field(
        default=False,
        description="Use a local SQLite file (tests / local hacking w/o Postgres).",
    )

    def resolve_sync_url(self) -> str:
        """Return a sync SQLAlchemy URL compatible with the current backend.

        When SQLite is enabled, we return a plain `sqlite:///` URL so the
        Celery worker (which uses sync SQLAlchemy) can hit the same DB.
        """
        if self.use_sqlite:
            return "sqlite:///:memory:"
        return self.sync_database_url

    # Redis / Celery
    redis_url: str = Field(default="redis://redis:6379/0")
    celery_task_always_eager: bool = Field(
        default=False,
        description="Run tasks inline. Used in tests.",
    )

    # Object storage (S3-compatible)
    s3_endpoint: str = Field(default="http://localhost:9000")
    s3_region: str = Field(default="us-east-1")
    s3_access_key: str = Field(default="minioadmin")
    s3_secret_key: str = Field(default="minioadmin")
    s3_bucket_ingest: str = Field(default="raw-videos")
    s3_bucket_output: str = Field(default="processed-videos")
    s3_presign_expiry_seconds: int = Field(default=3600)

    # Limits
    max_upload_bytes: int = Field(default=2 * 1024 * 1024 * 1024)  # 2 GiB

    # HTTP
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"],
    )

    log_level: str = Field(default="INFO")

    # Worker
    workdir: str = Field(
        default="/tmp/transcoder",
        description="Local working dir for inputs/outputs on the worker.",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, list):
            return value
        raise ValueError(f"Unsupported CORS_ORIGINS type: {type(value)!r}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (avoids re-parsing env on every call)."""
    return Settings()
