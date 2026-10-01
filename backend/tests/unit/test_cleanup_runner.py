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
                (5, JobStatus.UPLOADING),
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
    assert removed == 2

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

    assert cleanup_runner.run_once() == 0
