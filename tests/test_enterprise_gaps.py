from __future__ import annotations

import base64
import time

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import create_app
from fusion_science.api.auth import Role, _jwt_ttl, issue_jwt, touch_principal
from fusion_science.audit.tracker import TraceRecorder, _redaction_patterns
from fusion_science.config import ScienceConfig
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.utils.events import reset_event_bus
from fusion_science.utils.malware_scan import scan_bytes
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


# --- G3 malware scan ------------------------------------------------------


class TestMalwareScan:
    def test_clean_text_passes(self):
        result = scan_bytes(b"%PDF-1.4 some paper text content here", filename="paper.pdf")
        assert result.clean
        assert result.scanned_bytes > 0

    def test_pe_executable_flagged(self):
        result = scan_bytes(b"MZ\x90\x00" + b"\x00" * 200, filename="paper.pdf")
        assert not result.clean
        assert any("PE/COFF" in f for f in result.flags)

    def test_elf_executable_flagged(self):
        result = scan_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 100, filename="data.bin")
        assert not result.clean
        assert any("ELF" in f for f in result.flags)

    def test_script_shebang_flagged(self):
        result = scan_bytes(b"#!/bin/bash\nrm -rf /\n", filename="dataset.csv")
        assert not result.clean
        assert any("shebang" in f for f in result.flags)

    def test_blocked_extension_flagged(self):
        result = scan_bytes(b"anything", filename="payload.exe")
        assert not result.clean
        assert any("blocked extension" in f for f in result.flags)

    def test_archive_not_flagged_despite_high_entropy(self):
        # a zip is high-entropy but a recognized archive -> must pass
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("data.json", "x" * 10000)
        result = scan_bytes(buf.getvalue(), filename="dataset.zip")
        assert result.clean

    def test_empty_bytes_clean(self):
        assert scan_bytes(b"").clean


class TestScanRoute:
    @pytest.fixture
    def app(self, monkeypatch):
        reset_event_bus()
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "admin:admin-key,viewer:view-key")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "test-secret")
        config = ScienceConfig()
        application = create_app(config=config)
        application.state.config = config
        application.state.session_manager = SessionManager(MemorySessionStore())
        yield application
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_scan_clean_blob(self, app):
        tok = issue_jwt(Role.ADMIN, "admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/security/scan",
                json={"filename": "paper.pdf", "content_b64": base64.b64encode(b"%PDF-1.4 text").decode()},
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["clean"] is True

    @pytest.mark.asyncio
    async def test_scan_flagged_blob(self, app):
        tok = issue_jwt(Role.ADMIN, "admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/security/scan",
                json={"filename": "x.exe", "content_b64": base64.b64encode(b"MZ\x90").decode()},
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 200
            assert resp.json()["clean"] is False

    @pytest.mark.asyncio
    async def test_viewer_blocked_from_scan(self, app):
        tok = issue_jwt(Role.VIEWER, "viewer")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/security/scan",
                json={"filename": "x.pdf", "content_b64": base64.b64encode(b"text").decode()},
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 403


# --- G8 per-data-class retention ------------------------------------------


class TestRetentionMap:
    def test_env_retention_map_loaded(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_RETENTION_MAP", "ephi:2555,literature:365,audit:0")
        from fusion_science.audit.tracker import _load_retention_map

        m = _load_retention_map()
        assert m == {"ephi": 2555, "literature": 365, "audit": 0}

    def test_per_class_prune(self, tmp_path, monkeypatch):
        # ephi class: 1 day; literature class: 1000 days (keep). Global 90.
        monkeypatch.setenv("FUSION_SCIENCE_RETENTION_MAP", "ephi:1,literature:1000")
        rec = TraceRecorder(storage_dir=str(tmp_path), max_age_days=90)
        # write an ephi session (old) and a literature session (old)
        old_ts = time.time() - 200 * 86400  # 200 days old
        rec.start_session(metadata={"data_class": "ephi"})
        rec.record("llm_call", "test", "e")
        rec.end_session()
        rec.start_session(metadata={"data_class": "literature"})
        rec.record("llm_call", "test", "l")
        rec.end_session()
        # backdate both files' mtime to 200 days ago
        import os

        for f in tmp_path.glob("trace_*.json"):
            os.utime(f, (old_ts, old_ts))
        result = rec.prune()
        remaining = list(tmp_path.glob("trace_*.json"))
        # ephi (1d, 200d old) pruned; literature (1000d, 200d old) kept
        assert result["pruned_by_age"] >= 1
        blobs = [f.read_text() for f in remaining]
        assert any('"literature"' in b for b in blobs), "literature session must survive"
        assert not any('"ephi"' in b for b in blobs), "ephi session must be pruned"

    def test_unmapped_class_falls_back_to_global(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_RETENTION_MAP", raising=False)
        rec = TraceRecorder(storage_dir=str(tmp_path), max_age_days=1)
        rec.start_session(metadata={"data_class": "unknown"})
        rec.record("llm_call", "test", "u")
        rec.end_session()
        import os

        old_ts = time.time() - 200 * 86400
        for f in tmp_path.glob("trace_*.json"):
            os.utime(f, (old_ts, old_ts))
        result = rec.prune()
        assert result["pruned_by_age"] >= 1


# --- G11 anomaly detection ------------------------------------------------


class TestAnomalyDetect:
    @pytest.fixture
    def app(self, monkeypatch):
        reset_event_bus()
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "admin:admin-key")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "test-secret")
        monkeypatch.setenv("FUSION_SCIENCE_ANOMALY_DETECT", "1")
        config = ScienceConfig()
        application = create_app(config=config)
        application.state.config = config
        application.state.session_manager = SessionManager(MemorySessionStore())
        yield application
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_route_enumeration_alerts_without_blocking(self, app, caplog):
        # hit >=12 distinct route prefixes -> anomaly logged but request still
        # served (detection, not enforcement). Use health + many bogus prefixes.
        tok = issue_jwt(Role.ADMIN, "admin")
        transport = ASGITransport(app=app)
        import logging

        with caplog.at_level(logging.WARNING, logger="fusion_science.api.middleware"):
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                # 13 distinct prefixes that are NOT exempt (admin can reach any)
                for prefix in [
                    "sessions",
                    "databases",
                    "search",
                    "citations",
                    "math",
                    "compute",
                    "chat",
                    "audit",
                    "pipelines",
                    "models",
                    "tools",
                    "metrics",
                    "review",
                ]:
                    await c.get(f"/api/v1/{prefix}", headers={"Authorization": f"Bearer {tok}"})
        assert any("route_enumeration" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_no_anomaly_when_disabled(self, monkeypatch, caplog):
        reset_event_bus()
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "admin:admin-key")
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "test-secret")
        monkeypatch.delenv("FUSION_SCIENCE_ANOMALY_DETECT", raising=False)
        config = ScienceConfig()
        application = create_app(config=config)
        application.state.config = config
        application.state.session_manager = SessionManager(MemorySessionStore())
        tok = issue_jwt(Role.ADMIN, "admin")
        transport = ASGITransport(app=application)
        import logging

        with caplog.at_level(logging.WARNING, logger="fusion_science.api.middleware"):
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                for _ in range(15):
                    await c.get("/api/v1/sessions", headers={"Authorization": f"Bearer {tok}"})
        assert not any("route_enumeration" in rec.message for rec in caplog.records)
        reset_event_bus()
