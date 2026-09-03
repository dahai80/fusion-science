from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import create_app
from fusion_science.api.auth import (
    Role,
    decode_jwt,
    describe_api_keys,
    issue_jwt,
    load_api_keys,
    role_allows,
)
from fusion_science.config import _SENTINEL_ENGINE_API_KEY, ScienceConfig, load_config
from fusion_science.core.gateway import LLMGateway
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.utils.events import reset_event_bus


@pytest.fixture
def app(monkeypatch):
    reset_event_bus()
    monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "science:sci-key,viewer:view-key,admin:admin-key")
    monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "test-secret")
    config = ScienceConfig()
    application = create_app(config=config)
    application.state.config = config
    application.state.gateway = LLMGateway(config)
    application.state.session_manager = SessionManager(MemorySessionStore())
    yield application
    reset_event_bus()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestRolePermissions:
    def test_admin_allows_all(self):
        assert role_allows(Role.ADMIN, "compute", "POST")
        assert role_allows(Role.ADMIN, "system", "DELETE")

    def test_science_allows_compute_blocks_system(self):
        assert role_allows(Role.SCIENCE, "compute", "POST")
        assert role_allows(Role.SCIENCE, "chat", "POST")
        assert not role_allows(Role.SCIENCE, "system", "GET")
        assert not role_allows(Role.SCIENCE, "security", "GET")

    def test_viewer_readonly_blocks_compute(self):
        assert role_allows(Role.VIEWER, "search", "GET")
        assert role_allows(Role.VIEWER, "models", "GET")
        assert not role_allows(Role.VIEWER, "compute", "POST")
        assert not role_allows(Role.VIEWER, "chat", "POST")


class TestApiKeyLoad:
    def test_multi_key_roles(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "science:abc,viewer:def")
        keys = load_api_keys()
        assert keys["abc"] == Role.SCIENCE
        assert keys["def"] == Role.VIEWER

    def test_legacy_single_key_is_admin(self, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_API_KEYS", raising=False)
        monkeypatch.setenv("FUSION_SCIENCE_API_KEY", "legacy")
        keys = load_api_keys()
        assert keys["legacy"] == Role.ADMIN

    def test_malformed_entry_ignored(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "science:ok,badrole:bad,viewer:good")
        keys = load_api_keys()
        assert "ok" in keys
        assert "bad" not in keys
        assert "good" in keys

    def test_key_file_newline_separated(self, monkeypatch, tmp_path):
        kf = tmp_path / "keys.txt"
        kf.write_text("science:abc\nviewer:def\n")
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS_FILE", str(kf))
        monkeypatch.delenv("FUSION_SCIENCE_API_KEYS", raising=False)
        keys = load_api_keys()
        assert keys["abc"] == Role.SCIENCE
        assert keys["def"] == Role.VIEWER

    def test_key_file_wins_over_env(self, monkeypatch, tmp_path):
        # file entries override env — rotation: rewrite file, new key live
        kf = tmp_path / "keys.txt"
        kf.write_text("science:env-key\n")
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "science:env-key")
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS_FILE", str(kf))
        assert "env-key" in load_api_keys()
        # operator rewrites the file to rotate the science key
        kf.write_text("science:new-rotated-key\n")
        keys = load_api_keys()
        assert "new-rotated-key" in keys
        assert "env-key" not in keys

    def test_missing_key_file_warns(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS_FILE", str(tmp_path / "nope.txt"))
        monkeypatch.delenv("FUSION_SCIENCE_API_KEYS", raising=False)
        # no crash, empty result
        assert load_api_keys() == {}


class TestJwt:
    def test_roundtrip(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "s")
        tok = issue_jwt(Role.SCIENCE, "alice")
        p = decode_jwt(tok)
        assert p is not None
        assert p.role == Role.SCIENCE
        assert "alice" in p.subject

    def test_tamper_rejected(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "s")
        tok = issue_jwt(Role.ADMIN, "x")
        assert decode_jwt(tok[:-2] + "zz") is None

    def test_wrong_secret_rejected(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "s1")
        tok = issue_jwt(Role.ADMIN, "x")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "s2")
        assert decode_jwt(tok) is None


class TestAuthRoute:
    @pytest.mark.asyncio
    async def test_token_exchange_valid_key(self, client):
        resp = await client.post("/api/v1/auth/token", json={"api_key": "sci-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "science"
        assert body["token_type"] == "bearer"
        assert decode_jwt(body["access_token"]) is not None

    @pytest.mark.asyncio
    async def test_token_exchange_invalid_key(self, client):
        resp = await client.post("/api/v1/auth/token", json={"api_key": "nope"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_token_exchange_escelation_denied(self, client):
        # viewer key cannot mint a science token
        resp = await client.post(
            "/api/v1/auth/token",
            json={"api_key": "view-key", "role": "science"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_token_exchange_downgrade_allowed(self, client):
        # admin key can mint a viewer token
        resp = await client.post(
            "/api/v1/auth/token",
            json={"api_key": "admin-key", "role": "viewer"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"


class TestRbacEnforcement:
    @pytest.mark.asyncio
    async def test_viewer_blocked_from_compute(self, client):
        # /api/v1/compute — viewer has no compute permission
        resp = await client.post(
            "/api/v1/compute",
            json={"code": "print(1)"},
            headers={"X-API-Key": "view-key"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_science_allowed_on_compute(self, client):
        # science may compute — expect to pass RBAC (may 4xx from route logic,
        # but NOT 403)
        resp = await client.post(
            "/api/v1/compute",
            json={"code": "print(1)"},
            headers={"X-API-Key": "sci-key"},
        )
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_admin_allowed_on_compute(self, client):
        resp = await client.post(
            "/api/v1/compute",
            json={"code": "print(1)"},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_no_key_non_loopback_rejected(self, app):
        # httpx ASGI client appears as 127.0.0.1 (loopback) + keys are set, so a
        # missing credential is rejected (loopback-keyless only applies when NO
        # keys are provisioned).
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/sessions", json={"title": "T"})
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_jwt_accepted(self, client):
        # mint a science token, use it as Bearer
        tok = issue_jwt(Role.SCIENCE, "tester")
        resp = await client.post(
            "/api/v1/sessions",
            json={"title": "T"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_whoami_with_jwt(self, client):
        tok = issue_jwt(Role.VIEWER, "who")
        resp = await client.get(
            "/api/v1/auth/whoami",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "viewer"
        assert "who" in body["subject"]


class TestKeyRotation:
    def test_describe_masks_keys(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "science:abcdefghijklmnop,viewer:vwxyz12345")
        keys = load_api_keys()
        summary = describe_api_keys(keys)
        assert summary["total"] == 2
        assert summary["by_role"]["science"] == 1
        assert summary["by_role"]["viewer"] == 1
        # keys masked — no full secret
        joined = " ".join(k["key"] for k in summary["keys"])
        assert "abcdefghijklmnop" not in joined

    @pytest.mark.asyncio
    async def test_rotate_endpoint_admin_ok(self, client):
        resp = await client.post(
            "/api/v1/security/rotate-keys",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rotated"] is True
        assert body["by_role"]["admin"] == 1
        assert body["by_role"]["science"] == 1
        assert body["by_role"]["viewer"] == 1

    @pytest.mark.asyncio
    async def test_rotate_endpoint_viewer_blocked(self, client):
        # viewer has no security permission → 403
        resp = await client.post(
            "/api/v1/security/rotate-keys",
            headers={"X-API-Key": "view-key"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_rotate_reflects_file_rewrite(self, app, tmp_path, monkeypatch):
        # live rotation: rewrite key file, rotate endpoint sees the new key
        kf = tmp_path / "keys.txt"
        kf.write_text("admin:admin-key\n")
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS_FILE", str(kf))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # initially only admin-key
            r1 = await c.post("/api/v1/security/rotate-keys", headers={"X-API-Key": "admin-key"})
            assert r1.json()["total"] == 1
            # operator rotates in a science key
            kf.write_text("admin:admin-key\nscience:fresh-key\n")
            r2 = await c.post("/api/v1/security/rotate-keys", headers={"X-API-Key": "admin-key"})
            body = r2.json()
            assert body["total"] == 2
            assert body["by_role"]["science"] == 1
            # fresh key now works on a science route
            r3 = await c.post("/api/v1/sessions", json={"title": "T"}, headers={"X-API-Key": "fresh-key"})
            assert r3.status_code == 200


class TestKeychainSecrets:
    def test_jwt_secret_from_keychain(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_KEYCHAIN", "1")
        monkeypatch.delenv("FUSION_SCIENCE_JWT_SECRET", raising=False)
        monkeypatch.setattr(
            "fusion_science.utils.keychain.retrieve_key",
            lambda name: "kc-jwt-secret" if name == "jwt_secret" else None,
        )
        tok = issue_jwt(Role.ADMIN, "kc")
        p = decode_jwt(tok)
        assert p is not None and p.role == Role.ADMIN

    def test_jwt_secret_env_beats_keychain(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_KEYCHAIN", "1")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "env-secret")
        monkeypatch.setattr(
            "fusion_science.utils.keychain.retrieve_key",
            lambda name: "kc-secret",
        )
        # env wins
        tok = issue_jwt(Role.ADMIN, "x")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "env-secret")
        assert decode_jwt(tok) is not None
        # different secret should fail
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "kc-secret")
        assert decode_jwt(tok) is None

    def test_config_engine_key_from_keychain(self, monkeypatch, tmp_path):
        # FUSION_SCIENCE_KEYCHAIN=1, sentinel key → keychain resolves it
        monkeypatch.setenv("FUSION_SCIENCE_KEYCHAIN", "1")
        monkeypatch.delenv("FUSION_SCIENCE_ENGINE_API_KEY", raising=False)
        monkeypatch.setattr(
            "fusion_science.utils.keychain.retrieve_key",
            lambda name: "kc-engine-key" if name == "engine_api_key" else None,
        )
        # block the MLX resolver so the test doesn't hit the network
        monkeypatch.setattr("fusion_science.config._resolve_from_mlx", lambda c: None)
        cfg = load_config()
        assert cfg.engine_api_key == "kc-engine-key"

    def test_keychain_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_KEYCHAIN", raising=False)
        monkeypatch.setattr(
            "fusion_science.utils.keychain.retrieve_key",
            lambda name: "should-not-be-used",
        )
        monkeypatch.setattr("fusion_science.config._resolve_from_mlx", lambda c: None)
        cfg = load_config()
        # keychain not consulted; MLX blocked; stays sentinel
        assert cfg.engine_api_key == _SENTINEL_ENGINE_API_KEY
