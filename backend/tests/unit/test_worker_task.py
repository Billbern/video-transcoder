"""Unit tests for the Celery `transcode_job` task.

The task body is exercised end-to-end with a tempfile-backed sync SQLite and
mocked S3 / FFmpeg. We invoke `transcode_job.run(...)` directly so we don't
need a live Celery worker; the function is plain Python and `.run` bypasses
the Celery machinery (no broker, no retries).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.models.job import Base, Job, JobStatus
from app.workers import tasks as worker_tasks
from app.workers.tasks import transcode_job
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def sync_db(tmp_path: Path):
    """A sync SQLite engine pointing at a tempfile, schema created."""
    db_path = tmp_path / "worker.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    Sess: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
    yield Sess, engine
    engine.dispose()


@pytest.fixture
def seeded_job(sync_db) -> tuple[str, sessionmaker[Session]]:
    """Create a QUEUED Job and return (job_id_string, sync_session_factory)."""
    Sess, _ = sync_db
    job_id = uuid.uuid4()
    with Sess() as session:
        session.add(
            Job(
                id=job_id,
                filename="sample.mp4",
                content_type="video/mp4",
                file_size=4096,
                target_preset="720p",
                ingest_object_key=f"jobs/{job_id}/source.mp4",
                status=JobStatus.QUEUED,
            )
        )
        session.commit()
    return str(job_id), Sess


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "workdir"
    d.mkdir()
    return d


@pytest.fixture
def patched_worker_session(sync_db, monkeypatch: pytest.MonkeyPatch):
    """Point `_get_sync_factory` at our tempfile-backed session.

    `worker_tasks.reset_sync_session()` is called first so any cached engine
    is disposed before we substitute the factory.
    """
    _, engine = sync_db
    Sess = sessionmaker(bind=engine, expire_on_commit=False)
    worker_tasks.reset_sync_session()
    monkeypatch.setattr(worker_tasks, "_get_sync_factory", lambda: Sess)
    return Sess


# --- Helpers ---------------------------------------------------------------


def _mp4_head() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24  # 32 bytes total


def _evil_head() -> bytes:
    return b"NOT-A-VIDEO-FILE" + b"\x00" * 16


def _patch_s3(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    """Patch `S3Service` methods on the worker module with sync fakes.

    Uses `MagicMock` so `self` is auto-handled when Python resolves the
    method on instances. The provided callables are passed as `side_effect`;
    pass `None` (or a value) for `return_value` semantics.
    """
    for name, fn in overrides.items():
        mock = MagicMock(side_effect=fn)
        monkeypatch.setattr(worker_tasks.S3Service, name, mock)


# --- Happy path -----------------------------------------------------------


class TestHappyPath:
    def test_marks_processing_then_completed(
        self,
        seeded_job: tuple[str, sessionmaker[Session]],
        workdir: Path,
        patched_worker_session: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job_id, _ = seeded_job
        Sess = patched_worker_session
        input_data = _mp4_head() + b"\x00" * 4096

        def fake_download_to_file(*, bucket, key, dest_path):
            Path(dest_path).write_bytes(input_data)

        def fake_get_object_range(*, bucket, key, start, end):
            return input_data[start : end + 1]

        def fake_upload_fileobj(*, bucket, key, data, content_type=None):
            if hasattr(data, "read"):
                data.read()

        async def fake_transcode(self, input_path, output_path):
            output_path.write_bytes(b"FAKE_OUTPUT_MP4")
            from app.services.ffmpeg_processor import TranscodeResult

            return TranscodeResult(output_path=output_path, returncode=0, stderr="")

        _patch_s3(
            monkeypatch,
            download_to_file=fake_download_to_file,
            upload_fileobj=fake_upload_fileobj,
            get_object_range=fake_get_object_range,
        )
        monkeypatch.setattr(worker_tasks.FFmpegProcessor, "transcode", fake_transcode)

        from app.config import get_settings

        get_settings().workdir = str(workdir)

        result = transcode_job.run(job_id)

        assert result["status"] == "completed"
        assert result["output_key"] == f"{job_id}/output.mp4"

        with Sess() as session:
            job = session.get(Job, uuid.UUID(job_id))
            assert job is not None
            assert job.status == JobStatus.COMPLETED
            assert job.output_object_key == f"{job_id}/output.mp4"
            assert job.completed_at is not None
            assert job.error_code is None
            assert job.error_message is None


# --- Permanent failure: bad magic bytes -----------------------------------


class TestFormatFailure:
    def test_marks_unsupported_format(
        self,
        seeded_job: tuple[str, sessionmaker[Session]],
        workdir: Path,
        patched_worker_session: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job_id, _ = seeded_job
        Sess = patched_worker_session
        evil = _evil_head()

        def fake_get_object_range(*, bucket, key, start, end):
            return evil[start : end + 1]

        def fake_download_to_file(*, bucket, key, dest_path):
            Path(dest_path).write_bytes(evil)

        _patch_s3(
            monkeypatch,
            get_object_range=fake_get_object_range,
            download_to_file=fake_download_to_file,
        )

        from app.config import get_settings

        get_settings().workdir = str(workdir)

        result = transcode_job.run(job_id)

        assert result["status"] == "failed"
        assert result["code"] == "1001"  # UNSUPPORTED_FORMAT

        with Sess() as session:
            job = session.get(Job, uuid.UUID(job_id))
            assert job is not None
            assert job.status == JobStatus.FAILED
            assert job.error_code == "1001"
            assert "Unrecognized" in (job.error_message or "")


# --- Permanent failure: FFmpeg error --------------------------------------


class TestFFmpegFailure:
    def test_persists_stderr_preview_and_log(
        self,
        seeded_job: tuple[str, sessionmaker[Session]],
        workdir: Path,
        patched_worker_session: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.ffmpeg_processor import FFmpegError

        job_id, _ = seeded_job
        Sess = patched_worker_session
        good = _mp4_head() + b"\x00" * 4096

        def fake_get_object_range(*, bucket, key, start, end):
            return good[start : end + 1]

        def fake_download_to_file(*, bucket, key, dest_path):
            Path(dest_path).write_bytes(good)

        async def fake_transcode(self, input_path, output_path):
            raise FFmpegError(
                returncode=1,
                stderr="[error] Invalid data found when processing input\n" * 50,
            )

        _patch_s3(
            monkeypatch,
            get_object_range=fake_get_object_range,
            download_to_file=fake_download_to_file,
        )
        monkeypatch.setattr(worker_tasks.FFmpegProcessor, "transcode", fake_transcode)

        from app.config import get_settings

        get_settings().workdir = str(workdir)

        result = transcode_job.run(job_id)

        assert result["status"] == "failed"
        assert result["code"] == "1003"

        with Sess() as session:
            job = session.get(Job, uuid.UUID(job_id))
            assert job is not None
            assert job.status == JobStatus.FAILED
            assert job.error_code == "1003"
            assert job.ffmpeg_stderr is not None
            assert "[full log:" in job.ffmpeg_stderr
            log_path = workdir / "ffmpeg-logs" / f"{job_id}.log"
            assert log_path.exists()
            assert "Invalid data" in log_path.read_text()


# --- Transient failure: storage error during preflight ---------------------


class TestTransientRetry:
    def test_preflight_storage_error_calls_retry(
        self,
        seeded_job: tuple[str, sessionmaker[Session]],
        workdir: Path,
        patched_worker_session: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.storage import StorageError
        from celery.exceptions import Retry

        job_id, _ = seeded_job

        def fake_get_object_range(*, bucket, key, start, end):
            raise StorageError("simulated network blip")

        def fake_download_to_file(*, bucket, key, dest_path):
            pass  # not reached

        _patch_s3(
            monkeypatch,
            get_object_range=fake_get_object_range,
            download_to_file=fake_download_to_file,
        )

        retry_mock = MagicMock(side_effect=Retry())

        # Celery's `request` is a property bound to the per-task context.
        # `push_request` lets us inject a custom context for the duration of
        # the call (retries=0).
        transcode_job.push_request(retries=0)
        try:
            monkeypatch.setattr(transcode_job, "retry", retry_mock)
            monkeypatch.setattr(transcode_job, "max_retries", 3)

            from app.config import get_settings

            get_settings().workdir = str(workdir)

            with pytest.raises(Retry):
                transcode_job.run(job_id)

            retry_mock.assert_called_once()
            _, kwargs = retry_mock.call_args
            assert kwargs["countdown"] >= 10
        finally:
            transcode_job.pop_request()


class TestTransientExhausted:
    def test_max_retries_leads_to_failed_state(
        self,
        seeded_job: tuple[str, sessionmaker[Session]],
        workdir: Path,
        patched_worker_session: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When `self.retry` exhausts retries, the job is marked FAILED."""
        from app.services.storage import StorageError
        from celery.exceptions import MaxRetriesExceededError

        job_id, _ = seeded_job
        Sess = patched_worker_session

        def fake_get_object_range(*, bucket, key, start, end):
            raise StorageError("simulated outage")

        def fake_download_to_file(*, bucket, key, dest_path):
            pass

        _patch_s3(
            monkeypatch,
            get_object_range=fake_get_object_range,
            download_to_file=fake_download_to_file,
        )

        def fake_retry(*args, **kwargs):
            raise MaxRetriesExceededError()

        transcode_job.push_request(retries=3)
        try:
            monkeypatch.setattr(transcode_job, "retry", fake_retry)
            monkeypatch.setattr(transcode_job, "max_retries", 3)

            from app.config import get_settings

            get_settings().workdir = str(workdir)

            result = transcode_job.run(job_id)

            assert result["status"] == "failed"
            assert result["code"] == "1005"  # STORAGE_UNAVAILABLE

            with Sess() as session:
                job = session.get(Job, uuid.UUID(job_id))
                assert job is not None
                assert job.status == JobStatus.FAILED
                assert job.error_code == "1005"
        finally:
            transcode_job.pop_request()


# --- Missing job ----------------------------------------------------------


class TestMissingJob:
    def test_returns_missing_for_unknown_id(
        self,
        workdir: Path,
        patched_worker_session: sessionmaker[Session],
    ) -> None:
        from app.config import get_settings

        get_settings().workdir = str(workdir)
        result = transcode_job.run(str(uuid.uuid4()))
        assert result["status"] == "missing"


# --- Internal error path ---------------------------------------------------


class TestInternalError:
    def test_unexpected_exception_marks_internal(
        self,
        seeded_job: tuple[str, sessionmaker[Session]],
        workdir: Path,
        patched_worker_session: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job_id, _ = seeded_job
        Sess = patched_worker_session

        def fake_get_object_range(*, bucket, key, start, end):
            raise RuntimeError("boom")

        _patch_s3(monkeypatch, get_object_range=fake_get_object_range)

        from app.config import get_settings

        get_settings().workdir = str(workdir)

        result = transcode_job.run(job_id)

        assert result["status"] == "failed"
        assert result["code"] == "1999"

        with Sess() as session:
            job = session.get(Job, uuid.UUID(job_id))
            assert job is not None
            assert job.status == JobStatus.FAILED
            assert job.error_code == "1999"
            assert "boom" in (job.error_message or "")
