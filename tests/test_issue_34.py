from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from fusion_science.config import load_config

_ALIAS_ENV = ("FUSION_CLIENT_KEY", "FUSION_MLX_API_KEY", "MLX_API_KEY", "FUSION_PROFILE")


def _clean_env():
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("FUSION_SCIENCE_") and not k.startswith("FUSION_SCI_") and k not in _ALIAS_ENV
    }


class TestIssue34ConfigResolution:
    def test_dev_missing_key_empty_with_warning(self):
        # Acceptance: dev missing key = "" (anonymous dev warning from
        # fusion_core.client_key), not a sentinel literal.
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("fusion_science.config.resolve_api_key", return_value=""),
            patch("fusion_science.config._auto_detect_model"),
        ):
            config = load_config(path="/nonexistent/path.yml")
        assert config.engine_api_key == ""

    def test_prod_missing_key_raises(self):
        # Acceptance: FUSION_PROFILE=prod missing key = RuntimeError naming
        # the variable and the fix path (config-conventions §3).
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        env["FUSION_PROFILE"] = "prod"
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(RuntimeError, match="FUSION_CLIENT_KEY"),
        ):
            load_config(path="/nonexistent/path.yml")

    def test_prod_with_client_key_works(self):
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        env["FUSION_PROFILE"] = "prod"
        env["FUSION_CLIENT_KEY"] = "fm-prod-key"
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.engine_api_key == "fm-prod-key"

    def test_client_key_alias_chain_respected(self):
        # FUSION_MLX_API_KEY legacy alias still resolves (migration window).
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        env["FUSION_MLX_API_KEY"] = "legacy-key"
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.engine_api_key == "legacy-key"

    def test_explicit_engine_api_key_env_wins(self):
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        env["FUSION_SCIENCE_ENGINE_API_KEY"] = "explicit-key"
        env["FUSION_CLIENT_KEY"] = "client-key"
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.engine_api_key == "explicit-key"

    def test_service_port_env(self):
        # fusion_core.service_port reads FUSION_SCIENCE_PORT (what start.sh uses).
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        env["FUSION_SCIENCE_PORT"] = "11555"
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.api_port == 11555

    def test_service_port_default_unchanged(self):
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.api_port == 11462

    def test_data_dir_re_roots_default_paths(self):
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        env["FUSION_SCIENCE_DATA_DIR"] = "/tmp/fs-science-data"
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.cache_dir == "/tmp/fs-science-data"
        assert config.session_db_path == "/tmp/fs-science-data/sessions.db"
        assert config.trace_dir == "/tmp/fs-science-data/traces"
        assert config.provenance_dir == "/tmp/fs-science-data/provenance"

    def test_data_dir_keeps_explicit_sub_paths(self):
        # An explicit FUSION_SCIENCE_SESSION_DB_PATH is never re-rooted.
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        env["FUSION_SCIENCE_DATA_DIR"] = "/tmp/fs-science-data"
        env["FUSION_SCIENCE_SESSION_DB_PATH"] = "/tmp/custom/sessions.db"
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.session_db_path == "/tmp/custom/sessions.db"
        assert config.trace_dir == "/tmp/fs-science-data/traces"

    def test_no_data_dir_env_keeps_existing_paths(self):
        env = _clean_env()
        env.pop("FUSION_OFFLINE_MODE", None)
        with patch.dict(os.environ, env, clear=True), patch("fusion_science.config._auto_detect_model"):
            config = load_config(path="/nonexistent/path.yml")
        assert config.cache_dir == "~/.cache/fusion-science"
        assert config.session_db_path == "~/.cache/fusion-science/sessions.db"
        assert config.trace_dir == "~/.cache/fusion-science/traces"
        assert config.provenance_dir == "~/.cache/fusion-science/provenance"
