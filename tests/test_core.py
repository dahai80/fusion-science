"""Tests for the core engine module."""

from __future__ import annotations

import pytest

from fusion_science.core.engine import ScienceEngine, ModelConfig, LLMResponse


class TestModelConfig:
    """Test ModelConfig dataclass."""

    def test_default_config(self):
        config = ModelConfig()
        assert config.name == "qwen3.5-9b"
        assert config.base_url == "http://localhost:11434/v1"
        assert config.temperature == 0.3
        assert config.max_tokens == 8192

    def test_custom_config(self):
        config = ModelConfig(name="test-model", temperature=0.5)
        assert config.name == "test-model"
        assert config.temperature == 0.5
        assert config.max_tokens == 8192  # Default preserved


class TestLLMResponse:
    """Test LLMResponse dataclass."""

    def test_default_response(self):
        resp = LLMResponse(content="test")
        assert resp.content == "test"
        assert resp.tool_calls == []
        assert resp.finish_reason == "stop"
        assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}

    def test_custom_response(self):
        resp = LLMResponse(
            content="result",
            tool_calls=[{"id": "call_1"}],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        assert resp.content == "result"
        assert len(resp.tool_calls) == 1
        assert resp.finish_reason == "tool_calls"
        assert resp.usage["prompt_tokens"] == 100


class TestScienceEngine:
    """Test ScienceEngine."""

    def test_engine_init(self):
        engine = ScienceEngine()
        assert engine.config.name == "qwen3.5-9b"
        assert engine._client is None

    def test_build_science_prompt(self):
        engine = ScienceEngine()
        messages = engine.build_science_prompt("Analyze this sequence", "ATCG")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "ATCG" in messages[1]["content"]
        assert "Analyze this sequence" in messages[1]["content"]

    def test_build_science_prompt_no_context(self):
        engine = ScienceEngine()
        messages = engine.build_science_prompt("Test task")
        assert len(messages) == 2
        assert messages[1]["content"] == "Test task"

    def test_analyze_sequence(self):
        engine = ScienceEngine()
        # This just tests the prompt construction, not actual LLM call
        messages = engine.build_science_prompt(
            "Analyze the following biological sequence data and provide key insights:",
            "ATCG",
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"