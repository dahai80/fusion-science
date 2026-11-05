from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fusion_science.api.app import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestWebConsole:
    def test_serves_html_without_auth(self, client):
        # The static shell is exempt from APIKeyMiddleware (no secrets inside);
        # it must be reachable without an API key.
        res = client.get("/web")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "Fusion-Science Console" in res.text

    def test_html_references_all_three_pages(self, client):
        # Issue #40 acceptance: exec console, admin page, viz gallery.
        html = client.get("/web").text
        assert "jupyter/execute" in html
        assert "mirrors/latency" in html
        assert "security/keys" in html
        assert "viz/molecule/smiles" in html
        assert "viz/protein" in html

    def test_api_still_gated_without_key(self, client):
        # Exempting the shell must not leak into the API: protected routes
        # still fail closed without credentials.
        res = client.get("/api/v1/system/status")
        assert res.status_code in (401, 403)

    def test_missing_static_file_returns_404_json(self):
        # Defensive path: if the package data is missing, the route answers a
        # structured 404 instead of raising.
        import fusion_science.api.routes.web as web_mod

        original = web_mod.STATIC_DIR
        web_mod.STATIC_DIR = original.parent / "does-not-exist"
        try:
            app = create_app()
            with TestClient(app) as c:
                res = c.get("/web")
                assert res.status_code == 404
                assert res.json()["detail"] == "web_console_not_built"
        finally:
            web_mod.STATIC_DIR = original
