from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import _set_process_title, create_app
from fusion_science.api.auth import Role, decode_jwt, issue_jwt
from fusion_science.config import ScienceConfig
from fusion_science.utils.events import reset_event_bus


@pytest.fixture
def clean_secret_env(monkeypatch):
    monkeypatch.delenv("FUSION_SCIENCE_JWT_SECRET", raising=False)
    monkeypatch.delenv("FUSION_SCIENCE_API_KEY", raising=False)
    monkeypatch.delenv("FUSION_SCIENCE_API_KEYS", raising=False)
    monkeypatch.delenv("FUSION_SCIENCE_API_KEYS_FILE", raising=False)
    monkeypatch.delenv("FUSION_SCIENCE_KEYCHAIN", raising=False)
    monkeypatch.delenv("FUSION_PROFILE", raising=False)


class TestIssue32JwtSecretFailFast:
    # Issue #32: hardcoded "fusion-science-dev-secret" default removed.
    # Unset must mean empty; prod missing secret → RuntimeError with
    # variable name + how to obtain + doc path; dev → warning, JWT disabled.

    def test_no_default_secret_literal(self):
        with open("fusion_science/api/auth.py", encoding="utf-8") as fh:
            source = fh.read()
        assert "fusion-science-dev-secret" not in source

    def test_dev_issue_jwt_without_secret_raises(self, clean_secret_env):
        with pytest.raises(RuntimeError, match="signing secret not configured"):
            issue_jwt(Role.ADMIN, "x")

    def test_dev_decode_jwt_without_secret_returns_none(self, clean_secret_env):
        assert decode_jwt("aaa.bbb.ccc") is None

    def test_dev_warns_when_secret_missing(self, clean_secret_env, caplog):
        with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
            issue_jwt(Role.ADMIN, "x")
        assert any("FUSION_SCIENCE_JWT_SECRET" in r.message for r in caplog.records)

    def test_prod_issue_jwt_missing_secret_fail_fast(self, clean_secret_env, monkeypatch):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        with pytest.raises(RuntimeError) as exc:
            issue_jwt(Role.ADMIN, "x")
        msg = str(exc.value)
        assert "FUSION_SCIENCE_JWT_SECRET" in msg
        assert "config-conventions.md" in msg

    def test_prod_decode_jwt_missing_secret_no_raise(self, clean_secret_env, monkeypatch):
        # Request path must fail closed (401), not crash with 500.
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        assert decode_jwt("aaa.bbb.ccc") is None

    def test_secret_derived_from_api_key_still_works(self, clean_secret_env, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_API_KEY", "legacy-admin-key")
        tok = issue_jwt(Role.ADMIN, "legacy")
        p = decode_jwt(tok)
        assert p is not None and p.role == Role.ADMIN

    def test_explicit_secret_roundtrip(self, clean_secret_env, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "explicit-secret")
        tok = issue_jwt(Role.SCIENCE, "s")
        p = decode_jwt(tok)
        assert p is not None and p.role == Role.SCIENCE

    async def test_token_route_503_when_secret_missing(self, clean_secret_env, monkeypatch):
        reset_event_bus()
        monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "admin:admin-key")
        config = ScienceConfig()
        application = create_app(config=config)
        application.state.config = config
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/auth/token", json={"api_key": "admin-key"})
            assert r.status_code == 503
            assert r.json()["detail"] == "JWT signing secret not configured"
        reset_event_bus()


class TestIssue31ProcessTitle:
    # Issue #31: process title must show "fusion-science serve :<port>",
    # with a safe fallback when setproctitle is unavailable.

    def test_set_process_title(self):
        import setproctitle

        previous = setproctitle.getproctitle()
        try:
            _set_process_title(11462)
            assert setproctitle.getproctitle() == "fusion-science serve :11462"
        finally:
            setproctitle.setproctitle(previous)

    def test_set_process_title_fallback_safe(self, monkeypatch):
        import setproctitle

        previous = setproctitle.getproctitle()
        monkeypatch.setattr(setproctitle, "setproctitle", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        try:
            _set_process_title(11462)  # must not raise
        finally:
            monkeypatch.undo()
            setproctitle.setproctitle(previous)

    def test_setproctitle_declared_dependency(self):
        with open("pyproject.toml", encoding="utf-8") as fh:
            deps = fh.read()
        assert "setproctitle" in deps
