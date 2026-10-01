"""Unit tests for the zombie-upload cleanup runner.

The runner creates its own sync SQLAlchemy engine, so we point it at a
tempfile SQLite DB and seed via the sync engine directly. No event-loop
acrobatics required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.models.job import Base, Job, JobStatus
from app.workers import cleanup_runner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _StubStorage:
    """Minimal S3 stub that records delete calls without touching S3."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
        self.deleted: list[tuple[str, str]] = []

    def client(self):  # noqa: D401
        outer = self

        class _Client:
            def delete_object(self, **kwargs):  # noqa: ANN001
                outer.deleted.append((kwargs["Bucket"], kwargs["Key"]))
                return {}

        return _Client()


@pytest.fixture
def sync_db(tmp_path: Path):
    """Return a bound sessionmaker over a fresh SQLite file, with schema applied."""
    db_path = tmp_path / "cleanup.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, expire_on_commit=False)
    return Sess, engine


def _seed_zombies_and_fresh(Sess) -> None:
    now = datetime.now(tz=UTC)
    rows = [
        # Two zombies (old + UPLOADING), one fresh UPLOADING, one old PROCESSING.
        Job(
            id=uuid.uuid4(),
            filename=f"f{i}.mp4",
            content_type="video/mp4",
            file_size=1,
            target_preset="720p",
            ingest_object_key=f"jobs/{uuid.uuid4()}/source.mp4",
            status=status,
            created_at=now - timedelta(minutes=offset_min),
            updated_at=now - timedelta(minutes=offset_min),
        )
        for i, (offset_min, status) in enumerate(
            [
                (120, JobStatus.UPLOADING),
                (90, JobStatus.UPLOADING),
                # 1 minute old: should NOT be a zombie with a 4-min cutoff.
                (1, JobStatus.UPLOADING),
                (120, JobStatus.PROCESSING),
            ]
        )
    ]
    with Sess() as session:
        session.add_all(rows)
        session.commit()


def test_run_once_deletes_only_zombies(
    sync_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Sess, engine = sync_db
    _seed_zombies_and_fresh(Sess)

    # Force cleanup_runner to use our temp DB.
    from app.config import get_settings

    get_settings().workdir = str(tmp_path)

    monkeypatch.setattr(cleanup_runner, "S3Service", _StubStorage)
    # Avoid the runner building its own engine: monkeypatch create_engine.
    monkeypatch.setattr(cleanup_runner, "create_engine", lambda *a, **kw: engine)

    removed = cleanup_runner.run_once()
    assert removed.zombies_found == 2
    assert removed.objects_deleted == 2
    assert removed.objects_failed == 0

    # The non-UPLOADING and the fresh UPLOADING rows survive.
    with Sess() as session:
        remaining = session.query(Job).all()
        statuses = {row.status for row in remaining}
        assert JobStatus.UPLOADING in statuses  # the fresh one
        assert JobStatus.PROCESSING in statuses
        assert len(remaining) == 2


def test_run_once_returns_zero_when_no_zombies(
    sync_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Sess, engine = sync_db
    # Only a fresh UPLOADING row.
    now = datetime.now(tz=UTC)
    with Sess() as session:
        session.add(
            Job(
                id=uuid.uuid4(),
                filename="f.mp4",
                content_type="video/mp4",
                file_size=1,
                target_preset="720p",
                ingest_object_key=f"jobs/{uuid.uuid4()}/source.mp4",
                status=JobStatus.UPLOADING,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    from app.config import get_settings

    get_settings().workdir = str(tmp_path)
    monkeypatch.setattr(cleanup_runner, "S3Service", _StubStorage)
    monkeypatch.setattr(cleanup_runner, "create_engine", lambda *a, **kw: engine)

    assert cleanup_runner.run_once().zombies_found == 0


def test_failure_path_counts_objects_failed(
    sync_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S3 delete failures are recorded in `objects_failed` but don't block DB cleanup."""
    Sess, engine = sync_db
    _seed_zombies_and_fresh(Sess)

    from app.config import get_settings

    get_settings().workdir = str(tmp_path)
    monkeypatch.setattr(cleanup_runner, "create_engine", lambda *a, **kw: engine)

    class FailingStorage:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
            self.attempts = 0

        def client(self):  # noqa: D401
            outer = self

            class _Client:
                def delete_object(self, **kwargs):  # noqa: ANN001
                    outer.attempts += 1
                    from app.services.storage import StorageError

                    raise StorageError("simulated S3 outage")

            return _Client()

    monkeypatch.setattr(cleanup_runner, "S3Service", FailingStorage)

    report = cleanup_runner.run_once()
    assert report.zombies_found == 2
    assert report.objects_deleted == 0
    assert report.objects_failed == 2

    # Even though the S3 delete failed, the DB rows are still removed so the
    # user can re-upload with the same key. Operators reconcile S3 separately.
    with Sess() as session:
        zombies_remaining = (
            session.query(Job).filter(Job.status == JobStatus.UPLOADING).count()
        )
        assert zombies_remaining == 1  # only the fresh one survives


def test_custom_age_argument(
    sync_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`age=timedelta(minutes=4)` reliably excludes the 5-minute "fresh" row."""
    Sess, engine = sync_db
    _seed_zombies_and_fresh(Sess)

    from app.config import get_settings

    get_settings().workdir = str(tmp_path)
    monkeypatch.setattr(cleanup_runner, "create_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(cleanup_runner, "S3Service", _StubStorage)

    # A 4-minute cutoff should kill only the 90- and 120-min zombies, NOT the
    # 5-min fresh row (whose age exceeds the cutoff by a hair due to fixture
    # setup time, so we pick a slightly shorter age).
    from datetime import timedelta

    report = cleanup_runner.run_once(age=timedelta(minutes=4))
    assert report.zombies_found == 2


def test_celery_beat_schedule_registered() -> None:
    """Importing `cleanup_runner` registers the 15-minute Beat schedule."""
    from app.workers.celery_app import celery_app

    beat_schedule = celery_app.conf.beat_schedule
    assert "cleanup-zombie-uploads" in beat_schedule
    entry = beat_schedule["cleanup-zombie-uploads"]
    assert entry["task"] == "app.workers.cleanup_runner.cleanup_zombies_task"
    # Schedule object's `run_every` is in seconds; we want 15 minutes.
    run_every = entry["schedule"]
    seconds = getattr(run_every, "seconds", None) or getattr(
        run_every, "_seconds", None
    )
    assert seconds == 15 * 60


def test_cleanup_zombies_task_returns_dict_shape() -> None:
    """`cleanup_zombies_task` returns a JSON-serializable dict.

    We don't actually exercise the DB here — that's covered by
    `test_run_once_*` tests. This test just confirms the function exists
    and returns the expected shape so the Beat entry can call it.
    """
    # Patch `run_once` to return a known report so we don't touch the DB.
    from app.workers import cleanup_runner as cr

    class _StubReport:
        zombies_found = 0
        objects_deleted = 0
        objects_failed = 0

    monkeypatch_ = pytest.MonkeyPatch()
    try:
        monkeypatch_.setattr(cr, "run_once", lambda: _StubReport())
        result = cr.cleanup_zombies_task()
    finally:
        monkeypatch_.undo()

    assert isinstance(result, dict)
    assert set(result) == {"zombies_found", "objects_deleted", "objects_failed"}
    assert all(v == 0 for v in result.values())
