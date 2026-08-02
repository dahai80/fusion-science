from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import create_app
from fusion_science.config import ScienceConfig
from fusion_science.utils.events import reset_event_bus


@pytest.fixture
def app():
    reset_event_bus()
    config = ScienceConfig.from_env()
    application = create_app(config=config)
    yield application
    reset_event_bus()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthRoute:
    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "fusion-science"


class TestSessionRoutes:
    @pytest.mark.asyncio
    async def test_create_session(self, client):
        resp = await client.post("/api/v1/sessions", json={"title": "Test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["title"] == "Test"

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        await client.post("/api/v1/sessions", json={"title": "A"})
        resp = await client.get("/api/v1/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) >= 1

    @pytest.mark.asyncio
    async def test_get_session(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "X"})
        sid = create.json()["session_id"]
        resp = await client.get(f"/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "X"

    @pytest.mark.asyncio
    async def test_get_missing_session(self, client):
        resp = await client.get("/api/v1/sessions/nope")
        assert resp.status_code == 200
        assert resp.json()["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_delete_session(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "D"})
        sid = create.json()["session_id"]
        resp = await client.delete(f"/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_missing_session(self, client):
        resp = await client.delete("/api/v1/sessions/nope")
        assert resp.status_code == 200
        assert resp.json()["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_update_session_title(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "Old"})
        sid = create.json()["session_id"]
        resp = await client.patch(f"/api/v1/sessions/{sid}", json={"title": "New"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_api_key_missing_rejected(self, app):
        import os
        os.environ["FUSION_SCIENCE_API_KEY"] = "secret123"
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post("/api/v1/sessions", json={"title": "T"})
                assert resp.status_code == 401
        finally:
            del os.environ["FUSION_SCIENCE_API_KEY"]

    @pytest.mark.asyncio
    async def test_api_key_valid_passes(self, app):
        import os
        os.environ["FUSION_SCIENCE_API_KEY"] = "secret123"
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post(
                    "/api/v1/sessions",
                    json={"title": "T"},
                    headers={"X-API-Key": "secret123"},
                )
                assert resp.status_code == 200
        finally:
            del os.environ["FUSION_SCIENCE_API_KEY"]

    @pytest.mark.asyncio
    async def test_health_exempt_from_auth(self, app):
        import os
        os.environ["FUSION_SCIENCE_API_KEY"] = "secret123"
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/api/v1/health")
                assert resp.status_code == 200
        finally:
            del os.environ["FUSION_SCIENCE_API_KEY"]
