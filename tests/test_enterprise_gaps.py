from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import create_app
from fusion_science.api.auth import Role, issue_jwt
from fusion_science.audit.tracker import TraceRecorder
from fusion_science.config import ScienceConfig
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.utils.events import reset_event_bus
from fusion_science.utils.mfa import generate_totp, verify_subject_mfa, verify_totp

# --- G2 TLS config ---------------------------------------------------------


class TestTlsConfig:
    def test_tls_fields_default_empty(self):
        cfg = ScienceConfig()
        assert cfg.tls_certfile == ""
        assert cfg.tls_keyfile == ""

    def test_tls_env_override(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_TLS_CERTFILE", "/etc/ssl/sci.crt")
        monkeypatch.setenv("FUSION_SCIENCE_TLS_KEYFILE", "/etc/ssl/sci.key")
        from fusion_science.config import load_config

        cfg = load_config()
        assert cfg.tls_certfile == "/etc/ssl/sci.crt"
        assert cfg.tls_keyfile == "/etc/ssl/sci.key"


# --- G1 encryption-at-rest -------------------------------------------------


class TestEncryptAtRest:
    def test_roundtrip_encrypted(self, tmp_path, monkeypatch):
        # a real key -> AESGCM envelope (cryptography present via [oidc])
        monkeypatch.setenv("FUSION_SCIENCE_ENCRYPTION_KEY", "test-secret-key-123")
        rec = TraceRecorder(storage_dir=str(tmp_path), encrypt_at_rest=True)
        rec.start_session()
        rec.record("llm_call", "test", "probe", result_summary="ok")
        rec.end_session()
        # the on-disk file must NOT be plaintext JSON (magic prefix FS1)
        files = list(tmp_path.glob("trace_*.json"))
        assert files, "expected a saved trace file"
        blob = files[0].read_bytes()
        assert blob.startswith(b"FS1"), "audit file must be encrypted (magic prefix)"
        assert b"probe" not in blob, "plaintext must not leak into encrypted file"
        # read-back decrypts transparently
        sessions = rec.list_sessions()
        assert sessions and sessions[0]["entry_count"] >= 1

    def test_plaintext_store_still_reads_after_flag_on(self, tmp_path, monkeypatch):
        # write plaintext, then toggle flag on — old reads stay plaintext (no magic)
        monkeypatch.delenv("FUSION_SCIENCE_ENCRYPTION_KEY", raising=False)
        rec_plain = TraceRecorder(storage_dir=str(tmp_path), encrypt_at_rest=False)
        rec_plain.start_session()
        rec_plain.record("db_query", "test", "q1")
        rec_plain.end_session()
        # now read with encrypt_at_rest=True but no key — decrypt is a no-op on plaintext
        monkeypatch.setenv("FUSION_SCIENCE_ENCRYPTION_KEY", "k")
        rec_enc = TraceRecorder(storage_dir=str(tmp_path), encrypt_at_rest=True)
        sessions = rec_enc.list_sessions()
        assert sessions, "plaintext store must still read after flag toggled on"

    def test_no_key_degrades_to_plaintext(self, tmp_path, monkeypatch):
        # flag on but no key + no keychain -> plaintext write + warning, no crash
        monkeypatch.delenv("FUSION_SCIENCE_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("fusion_science.utils.crypto.get_key", lambda *_a, **_k: "", raising=False)
        rec = TraceRecorder(storage_dir=str(tmp_path), encrypt_at_rest=True)
        rec.start_session()
        rec.record("llm_call", "test", "probe")
        rec.end_session()
        blob = next(tmp_path.glob("trace_*.json")).read_bytes()
        assert not blob.startswith(b"FS1"), "no key -> plaintext, not encrypted"


# --- G6 MFA / TOTP ---------------------------------------------------------


class TestMfa:
    def test_totp_generate_verify(self):
        secret = "JBSWY3DPEHPK3PXP"  # base32
        code = generate_totp(secret)
        assert len(code) == 6
        assert verify_totp(secret, code)

    def test_totp_wrong_code_rejected(self):
        assert not verify_totp("JBSWY3DPEHPK3PXP", "000000")

    def test_totp_none_rejected(self):
        assert not verify_totp("JBSWY3DPEHPK3PXP", None)

    def test_mfa_not_required_passes(self, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_MFA_REQUIRED", raising=False)
        assert verify_subject_mfa("alice", None) is True

    def test_mfa_required_with_secret_passes(self, monkeypatch, tmp_path):
        secret = "JBSWY3DPEHPK3PXP"
        sf = tmp_path / "mfa.txt"
        sf.write_text(f"alice:{secret}\n")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_REQUIRED", "1")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_SECRETS_FILE", str(sf))
        code = generate_totp(secret)
        assert verify_subject_mfa("alice", code) is True

    def test_mfa_required_wrong_code_rejected(self, monkeypatch, tmp_path):
        sf = tmp_path / "mfa.txt"
        sf.write_text("alice:JBSWY3DPEHPK3PXP\n")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_REQUIRED", "1")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_SECRETS_FILE", str(sf))
        assert verify_subject_mfa("alice", "000000") is False

    def test_mfa_required_no_secret_fail_closed(self, monkeypatch, tmp_path):
        sf = tmp_path / "mfa.txt"
        sf.write_text("")  # no secret for alice
        monkeypatch.setenv("FUSION_SCIENCE_MFA_REQUIRED", "1")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_SECRETS_FILE", str(sf))
        assert verify_subject_mfa("alice", "123456") is False


# --- G6 MFA on /auth/token -------------------------------------------------


class TestMfaTokenRoute:
    @pytest.fixture
    def app(self, monkeypatch):
        reset_event_bus()
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "admin:admin-key,science:sci-key")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "test-secret")
        config = ScienceConfig()
        application = create_app(config=config)
        application.state.config = config
        application.state.session_manager = SessionManager(MemorySessionStore())
        yield application
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_token_without_mfa_when_required_rejected(self, app, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_MFA_REQUIRED", "1")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_SECRETS_FILE", "/nonexistent")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/auth/token", json={"api_key": "admin-key"})
            assert resp.status_code == 401
            assert "MFA" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_token_with_valid_totp_succeeds(self, app, monkeypatch, tmp_path):
        secret = "JBSWY3DPEHPK3PXP"
        sf = tmp_path / "mfa.txt"
        sf.write_text(f"apikey:admin-k:{secret}\n")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_REQUIRED", "1")
        monkeypatch.setenv("FUSION_SCIENCE_MFA_SECRETS_FILE", str(sf))
        code = generate_totp(secret)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/auth/token",
                json={"api_key": "admin-key", "subject": "apikey:admin-k", "totp": code},
            )
            assert resp.status_code == 200
            assert "access_token" in resp.json()


# --- G9 DSAR / right-to-erasure --------------------------------------------


class TestDsar:
    @pytest.fixture
    def app(self, monkeypatch):
        reset_event_bus()
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "admin:admin-key,viewer:view-key")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "test-secret")
        config = ScienceConfig()
        application = create_app(config=config)
        application.state.config = config
        mgr = SessionManager(MemorySessionStore())
        # seed sessions for two subjects
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.create_session(title="a", owner="alice"))
        loop.run_until_complete(mgr.create_session(title="b", owner="alice"))
        loop.run_until_complete(mgr.create_session(title="c", owner="bob"))
        loop.close()
        application.state.session_manager = mgr
        yield application
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_list_subject_sessions(self, app):
        tok = issue_jwt(Role.ADMIN, "admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/data-subject/alice/sessions",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["subject"] == "alice"
            assert body["count"] == 2

    @pytest.mark.asyncio
    async def test_purge_subject(self, app):
        tok = issue_jwt(Role.ADMIN, "admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.delete(
                "/api/v1/data-subject/alice",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["subject"] == "alice"
            assert body["count"] == 2
            # bob's sessions survive
            resp2 = await c.get(
                "/api/v1/data-subject/bob/sessions",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp2.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_purge_idempotent(self, app):
        tok = issue_jwt(Role.ADMIN, "admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.delete("/api/v1/data-subject/alice", headers={"Authorization": f"Bearer {tok}"})
            resp = await c.delete("/api/v1/data-subject/alice", headers={"Authorization": f"Bearer {tok}"})
            assert resp.status_code == 200
            assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_viewer_blocked_from_dsar(self, app):
        tok = issue_jwt(Role.VIEWER, "viewer")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.delete(
                "/api/v1/data-subject/alice",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 403
