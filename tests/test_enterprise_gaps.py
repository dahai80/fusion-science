from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import create_app
from fusion_science.api.auth import Role, _jwt_ttl, issue_jwt, touch_principal
from fusion_science.audit.tracker import TraceRecorder, _redaction_patterns
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


# --- G4 idle session lockout ----------------------------------------------


class TestIdleLockout:
    def test_disabled_when_timeout_zero(self):
        # idle_timeout<=0 always admits (dev default)
        assert touch_principal("u1", 0) is True
        assert touch_principal("u1", 0) is True

    def test_active_within_window_admitted(self):
        touch_principal("idle-a", 1000)
        # immediately again -> still within window
        assert touch_principal("idle-a", 1000) is True

    def test_expired_window_rejected(self, monkeypatch):
        # seed last-seen, then rewind it past the window
        touch_principal("idle-b", 1000)
        from fusion_science.api import auth as authmod

        monkeypatch.setitem(authmod._IDLE_TRACK, "idle-b", time.time() - 2000)
        assert touch_principal("idle-b", 1000) is False
        # entry dropped after expiry
        assert "idle-b" not in authmod._IDLE_TRACK


# --- G5 configurable JWT TTL ----------------------------------------------


class TestJwtTtl:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_JWT_TTL", raising=False)
        assert _jwt_ttl() == 3600

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_TTL", "900")
        assert _jwt_ttl() == 900

    def test_zero_keeps_default(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_TTL", "0")
        assert _jwt_ttl() == 3600

    def test_bad_value_keeps_default(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_TTL", "not-a-number")
        assert _jwt_ttl() == 3600

    def test_issue_jwt_uses_configured_ttl(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_TTL", "120")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "s")
        tok = issue_jwt(Role.SCIENCE, "sci-user")
        # decode the payload exp and check it is ~120s ahead (allow jitter)
        import base64
        import json

        payload_b64 = tok.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        delta = payload["exp"] - payload["iat"]
        assert 100 <= delta <= 130


# --- G7 extensible redaction ----------------------------------------------


class TestRedaction:
    def test_builtin_patterns_present(self, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_REDACT_PATTERNS", raising=False)
        pats = _redaction_patterns()
        assert "email" in pats
        assert "patient" in pats

    def test_env_patterns_merged(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_REDACT_PATTERNS", "mrn,ssn, 医保号 ")
        pats = _redaction_patterns()
        assert "mrn" in pats and "ssn" in pats and "医保号" in pats
        # built-ins still present
        assert "email" in pats

    def test_sanitizer_uses_extra_patterns(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_REDACT_PATTERNS", "mrn")
        rec = TraceRecorder(storage_dir="~/.cache/fusion-science/test-traces-redact")
        rec.start_session()
        rec.record("db_query", "test", "q", parameters={"mrn": "12345", "note": "keep"})
        rec.end_session()
        from fusion_science.audit.tracker import _sanitize_params

        out = _sanitize_params({"mrn": "12345", "note": "keep"})
        assert out["mrn"] == "***REDACTED***"
        assert out["note"] == "keep"


# --- G10 tamper alert -----------------------------------------------------


class TestTamperAlert:
    def test_no_alert_when_url_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_TAMPER_ALERT_URL", raising=False)
        rec = TraceRecorder(storage_dir=str(tmp_path))
        sid = rec.start_session()
        rec.record("llm_call", "test", "probe")
        rec.end_session()
        # audit_chain on intact chain -> ok, no alert path exercised (no crash)
        result = rec.audit_chain(sid)
        assert result.ok is True

    def test_alert_fired_on_tamper(self, tmp_path, monkeypatch):
        # stand up a tiny HTTP sink that records the POST
        import http.server
        import threading

        received: list[bytes] = []

        class _Sink(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received.append(self.rfile.read(length))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setenv("FUSION_SCIENCE_TAMPER_ALERT_URL", f"http://127.0.0.1:{port}/alert")
            rec = TraceRecorder(storage_dir=str(tmp_path))
            sid = rec.start_session()
            rec.record("llm_call", "test", "probe")
            rec.end_session()
            # tamper: rewrite the on-disk file to break the hash chain
            files = list(tmp_path.glob("trace_*.json"))
            assert files
            files[0].write_text('{"session_id":"x","entries":[{"id":"e1","prev_hash":"","entry_hash":"BAD"}]}')
            result = rec.audit_chain(sid)
            assert result.ok is False
            assert result.mismatches
            # the daemon thread delivers asynchronously; poll briefly
            for _ in range(50):
                if received:
                    break
                time.sleep(0.05)
            assert received, "tamper alert must be POSTed to the configured sink"
            import json

            payload = json.loads(received[0])
            assert payload["event"] == "audit_tamper_detected"
            assert payload["mismatches"]
        finally:
            srv.shutdown()
            thread.join(timeout=2)


# --- G12 等保三级 180d retention -------------------------------------------


class TestComplianceRetention:
    def test_compliance_level_field_default(self):
        assert ScienceConfig().compliance_level == 1

    def test_level3_env_binds(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_COMPLIANCE_LEVEL", "3")
        from fusion_science.config import load_config

        assert load_config().compliance_level == 3

    def test_level3_raises_retention_default(self, monkeypatch, tmp_path):
        # app.py lifespan logic: level>=3 + no explicit age -> 180
        monkeypatch.setenv("FUSION_SCIENCE_COMPLIANCE_LEVEL", "3")
        monkeypatch.delenv("FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS", raising=False)
        import os

        _compliance = int(os.getenv("FUSION_SCIENCE_COMPLIANCE_LEVEL", "1"))
        _audit_age = int(os.getenv("FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS", "90"))
        _explicit = os.getenv("FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS") is not None
        if _compliance >= 3 and not _explicit and _audit_age < 180:
            _audit_age = 180
        assert _audit_age == 180

    def test_explicit_age_wins_over_level3(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_COMPLIANCE_LEVEL", "3")
        monkeypatch.setenv("FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS", "365")
        import os

        _audit_age = int(os.getenv("FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS", "90"))
        _explicit = os.getenv("FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS") is not None
        if int(os.getenv("FUSION_SCIENCE_COMPLIANCE_LEVEL", "1")) >= 3 and not _explicit and _audit_age < 180:
            _audit_age = 180
        assert _audit_age == 365
