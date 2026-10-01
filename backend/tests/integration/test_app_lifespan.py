"""Tests for the FastAPI app factory and lifespan."""

from __future__ import annotations

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_create_app_includes_routes() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/api/initiate" in paths
    assert "/api/transcode" in paths
    assert "/api/jobs" in paths
    # /api/jobs/{job_id} is parameterized, so just check the prefix.
    assert any(p.startswith("/api/jobs/") for p in paths)


@pytest.mark.asyncio
async def test_app_swagger_docs_available() -> None:
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        r = await ac.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema["info"]["title"] == "VideoTranscode Pro API"
        # Each documented path from the spec.
        assert "/api/initiate" in schema["paths"]
        assert "/api/transcode" in schema["paths"]
        assert "/api/jobs" in schema["paths"]
