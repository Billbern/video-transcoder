"""Shared pytest fixtures."""

from __future__ import annotations

import os

# Force SQLite + eager Celery *before* any app import resolves its config.
os.environ.setdefault("USE_SQLITE", "1")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ.setdefault("S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("S3_ACCESS_KEY", "test")
os.environ.setdefault("S3_SECRET_KEY", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import dispose_engine, get_engine, get_session_factory  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _db_setup() -> AsyncIterator[None]:
    """Create tables on the SQLite test DB, drop on teardown.

    `autouse=True` so every test gets a clean schema regardless of whether it
    depends on the API client or the repository directly.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await dispose_engine()
        # Also tear down any sync engine the worker module may have built.
        from app.workers import tasks as _worker_tasks

        _worker_tasks.reset_sync_session()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An httpx client bound to the FastAPI ASGI app (no live server)."""
    from app.main import create_app

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Bare AsyncSession for tests that exercise the repository directly."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
