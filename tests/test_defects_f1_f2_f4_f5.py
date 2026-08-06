from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fusion_science.config import load_config
from fusion_science.core.context_manager import (
    MAX_MESSAGES_BEFORE_COMPRESS,
    ContextManager,
    count_message_tokens,
)
from fusion_science.core.gateway import LLMResponse
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.session.models import ResearchContext, ResearchSession
from fusion_science.utils.events import reset_event_bus


def _make_session(session_id: str, messages: list[dict]) -> ResearchSession:
    now = time.time()
    return ResearchSession(
        id=session_id,
        title="test",
        created_at=now,
        updated_at=now,
        messages=messages,
        context=ResearchContext(),
        artifacts=[],
        trace_ids=[],
    )


def _over_budget_messages(count: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 2000} for i in range(count)]


# ===================== F1: fit() is pure truncation, no LLM in sync path =====================


class TestFitNoLLM:
    def test_fit_truncates_without_calling_gateway(self):
        reset_event_bus()
        store = MemorySessionStore()
        mgr = SessionManager(store=store)
        gateway = MagicMock()
        gateway.chat = AsyncMock()

        cm = ContextManager(
            session_manager=mgr,
            gateway=gateway,
            context_window=4096,
            max_tokens=512,
            thinking_budget=512,
            model="qwen3.5-9b",
        )
        messages = _over_budget_messages(MAX_MESSAGES_BEFORE_COMPRESS + 5)
        store.save(_make_session("fit-sid", messages))

        original_tokens = count_message_tokens(messages, cm.model)
        fitted = cm.fit("fit-sid")
        fitted_tokens = count_message_tokens(fitted, cm.model)

        assert fitted_tokens < original_tokens
        assert len(fitted) < len(messages)
        gateway.chat.assert_not_called()
        reset_event_bus()


# ===================== F2: maybe_compress rewrites stored messages via LLM =====================


class TestMaybeCompress:
    @pytest.mark.asyncio
    async def test_maybe_compress_summarizes_and_rewrites(self):
        reset_event_bus()
        store = MemorySessionStore()
        mgr = SessionManager(store=store)
        gateway = MagicMock()
        gateway.chat = AsyncMock(return_value=LLMResponse(content="KNOWN_SUMMARY_TEXT", model="test"))

        cm = ContextManager(
            session_manager=mgr,
            gateway=gateway,
            context_window=4096,
            max_tokens=512,
            thinking_budget=512,
            model="qwen3.5-9b",
        )
        messages = _over_budget_messages(MAX_MESSAGES_BEFORE_COMPRESS + 5)
        store.save(_make_session("compress-sid", messages))

        before_count = len(mgr.get_messages("compress-sid"))
        result = await cm.maybe_compress("compress-sid")

        assert result is True
        gateway.chat.assert_awaited_once()

        after = mgr.get_messages("compress-sid")
        assert len(after) < before_count
        assert any("KNOWN_SUMMARY_TEXT" in m.get("content", "") for m in after)
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_maybe_compress_no_gateway_uses_fallback(self):
        reset_event_bus()
        store = MemorySessionStore()
        mgr = SessionManager(store=store)

        cm = ContextManager(
            session_manager=mgr,
            gateway=None,
            context_window=4096,
            max_tokens=512,
            thinking_budget=512,
            model="qwen3.5-9b",
        )
        messages = _over_budget_messages(MAX_MESSAGES_BEFORE_COMPRESS + 5)
        store.save(_make_session("nogw-sid", messages))

        before_count = len(mgr.get_messages("nogw-sid"))
        result = await cm.maybe_compress("nogw-sid")

        assert result is True
        after = mgr.get_messages("nogw-sid")
        assert len(after) < before_count
        assert any("summarized" in m.get("content", "") for m in after)
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_maybe_compress_skips_when_under_threshold(self):
        reset_event_bus()
        store = MemorySessionStore()
        mgr = SessionManager(store=store)
        gateway = MagicMock()
        gateway.chat = AsyncMock()

        cm = ContextManager(
            session_manager=mgr,
            gateway=gateway,
            context_window=4096,
            max_tokens=512,
            thinking_budget=512,
            model="qwen3.5-9b",
        )
        store.save(_make_session("small-sid", [{"role": "user", "content": "hi"}]))

        result = await cm.maybe_compress("small-sid")
        assert result is False
        gateway.chat.assert_not_awaited()
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_maybe_compress_retriggers_after_growth(self):
        reset_event_bus()
        store = MemorySessionStore()
        mgr = SessionManager(store=store)
        gateway = MagicMock()
        gateway.chat = AsyncMock(return_value=LLMResponse(content="KNOWN_SUMMARY_TEXT", model="test"))

        cm = ContextManager(
            session_manager=mgr,
            gateway=gateway,
            context_window=4096,
            max_tokens=512,
            thinking_budget=512,
            model="qwen3.5-9b",
        )
        messages = _over_budget_messages(MAX_MESSAGES_BEFORE_COMPRESS + 5)
        store.save(_make_session("recompress-sid", messages))

        result1 = await cm.maybe_compress("recompress-sid")
        assert result1 is True

        after = mgr.get_messages("recompress-sid")
        grown = after + _over_budget_messages(MAX_MESSAGES_BEFORE_COMPRESS + 5)
        store.save(_make_session("recompress-sid", grown))

        result2 = await cm.maybe_compress("recompress-sid")
        assert result2 is True
        assert gateway.chat.await_count == 2
        reset_event_bus()


# ===================== F4/F5: load_config auto-resolves from fusion-mlx =====================


def _clean_env():
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("FUSION_SCIENCE_") and not k.startswith("FUSION_SCI_") and k != "FUSION_OFFLINE_MODE"
    }


class TestLoadConfigMlxAutoDetect:
    def test_auto_detects_mlx_live(self):
        settings_path = Path.home() / ".fusion-mlx" / "settings.json"
        try:
            settings = json.loads(settings_path.read_text())
            key = settings.get("auth", {}).get("api_key", "")
            with httpx.Client(timeout=2.0) as c:
                r = c.get(
                    "http://localhost:11434/api/status",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "X-Fusion-Route": "fusion-science",
                    },
                )
                r.raise_for_status()
                status = r.json()
            expected_model = (status.get("loaded_models") or [""])[0]
        except Exception:
            pytest.skip("fusion-mlx not reachable")

        with patch.dict(os.environ, _clean_env(), clear=True):
            config = load_config(path="/nonexistent/path.yml")

        assert config.engine_api_key == key
        assert config.model_name == expected_model

    def test_mlx_down_keeps_defaults_nonfatal(self):
        with (
            patch.dict(os.environ, _clean_env(), clear=True),
            patch(
                "fusion_science.config._MLX_SETTINGS_PATH",
                Path("/nonexistent/settings.json"),
            ),
            patch("httpx.Client") as mock_client,
        ):
            mock_client.side_effect = httpx.ConnectError("connection refused")
            config = load_config(path="/nonexistent/path.yml")

        assert config.engine_api_key == "local"
        assert config.model_name == "qwen3.5-9b"

    def test_env_override_beats_auto_detect(self):
        env = dict(_clean_env())
        env["FUSION_SCIENCE_MODEL_NAME"] = "custom-model"
        env["FUSION_SCIENCE_ENGINE_API_KEY"] = "custom-key"
        with patch.dict(os.environ, env, clear=True):
            config = load_config(path="/nonexistent/path.yml")
        assert config.model_name == "custom-model"
        assert config.engine_api_key == "custom-key"
