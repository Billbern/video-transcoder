"""Testcontainers fixtures for Postgres + Redis.

These fixtures spin up real, ephemeral Postgres and Redis containers during
the test run. Per `docs/testing_strategies.md`:

    Integration Testing (Python - pytest + Testcontainers):
        Target: API endpoints, Database interactions.
        Method: Spin up real, ephemeral Postgres and Redis containers during
                the test run. Verify that an API call to /transcode actually
                creates a record in the DB and pushes a message to Redis.

The fixtures are *opt-in*: they require a working Docker daemon and skip with
a clear message if `DOCKER_HOST` is not reachable. To run them:

    pytest tests/integration/testcontainers_fixtures.py

In CI, the GitHub Actions runner has Docker pre-installed; locally this is a
no-op when Docker isn't available (CI tests the real path).
"""

from __future__ import annotations

import os
import socket

import pytest


def _docker_reachable() -> bool:
    """Best-effort check: try to connect to the Docker daemon.

    Returns True if a TCP connection to the default Docker socket succeeds
    within a short timeout. Used only to gate the Testcontainers fixtures
    (the actual container.start() will give a clearer error if needed).
    """
    host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if host.startswith("unix://"):
        path = host[len("unix://") :]
        try:
            return os.access(path, os.R_OK | os.W_OK)
        except OSError:
            return False
    if host.startswith("tcp://"):
        _, rest = host.split("tcp://", 1)
        host_port = rest.split("/", 1)[0]
        h, _, p = host_port.partition(":")
        try:
            with socket.create_connection((h, int(p or 2375)), timeout=0.5):
                return True
        except OSError:
            return False
    return False


# Skip the entire module if Docker isn't available.
docker_available = pytest.mark.skipif(
    os.environ.get("SKIP_TESTCONTAINERS") == "1" or not _docker_reachable(),
    reason="Docker not reachable; set SKIP_TESTCONTAINERS=0 to override",
)


@pytest.fixture(scope="session")
def postgres_container():
    """An ephemeral Postgres 16 container.

    Yields a libpq DSN. Skipped automatically when Docker isn't reachable.
    """
    from testcontainers.postgres import PostgresContainer

    pg = PostgresContainer("postgres:16-alpine")
    pg.start()
    try:
        # SQLAlchemy asyncpg driver.
        yield pg.get_connection_url(driver="asyncpg")
    finally:
        pg.stop()


@pytest.fixture(scope="session")
def redis_container():
    """An ephemeral Redis 7 container.

    Yields a redis:// URL. Skipped automatically when Docker isn't reachable.
    """
    from testcontainers.redis import RedisContainer

    rd = RedisContainer("redis:7-alpine")
    rd.start()
    try:
        yield f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
    finally:
        rd.stop()


@pytest.fixture
def app_under_test(postgres_container, redis_container, monkeypatch):
    """A FastAPI app instance wired to the ephemeral Postgres + Redis.

    Re-points the cached `Settings` at the Testcontainer DSNs and rebuilds
    the async engine + Celery broker connection. Use this in integration
    tests that exercise the full DB + queue path.
    """
    from app.config import get_settings
    from app.db import session as db_session
    from app.workers import celery_app as celery_app_mod

    settings = get_settings()
    settings.database_url = postgres_container
    settings.sync_database_url = postgres_container.replace("+asyncpg", "")
    settings.redis_url = redis_container
    settings.celery_task_always_eager = False  # exercise the broker path
    settings.use_sqlite = False

    # Drop any cached engines so the next call rebuilds against the new DSNs.
    db_session.dispose_engine()
    from app.workers import tasks as worker_tasks

    worker_tasks.reset_sync_session()

    # Rebuild the Celery app so the broker URL is fresh.
    celery_app_mod.celery_app = celery_app_mod._build_celery()

    from app.main import create_app

    app = create_app()
    yield app

    db_session.dispose_engine()
    worker_tasks.reset_sync_session()
    celery_app_mod.celery_app = celery_app_mod._build_celery()


__all__ = [
    "app_under_test",
    "docker_available",
    "postgres_container",
    "redis_container",
]
