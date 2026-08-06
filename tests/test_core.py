from __future__ import annotations

from fusion_science.config import ScienceConfig, load_config
from fusion_science.core.engine import LLMResponse, LLMResult, ScienceEngine
from fusion_science.core.gateway import LLMGateway


class TestLLMResponse:
    def test_default_response(self):
        resp = LLMResponse()
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.usage == {}
        assert resp.model == ""
        assert resp.finish_reason == ""

    def test_custom_response(self):
        resp = LLMResponse(
            content="result",
            tool_calls=[{"id": "call_1"}],
            usage={"prompt_tokens": 100},
            model="qwen3.5-9b",
            finish_reason="stop",
        )
        assert resp.content == "result"
        assert len(resp.tool_calls) == 1
        assert resp.model == "qwen3.5-9b"


class TestLLMResult:
    def test_default_result(self):
        r = LLMResult(raw="hello", parsed=None, error="")
        assert r.raw == "hello"
        assert r.parsed is None
        assert r.error == ""

    def test_result_with_parsed(self):
        r = LLMResult(raw='{"a":1}', parsed={"a": 1}, error="")
        assert r.parsed["a"] == 1


class TestScienceEngine:
    def test_engine_init(self):
        engine = ScienceEngine(model="qwen3.5-9b")
        assert engine.model == "qwen3.5-9b"
        assert engine.temperature == 0.3
        assert engine.max_tokens == 8192

    def test_engine_is_gateway_alias(self):
        assert ScienceEngine is LLMGateway

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


class TestScienceConfig:
    def test_default_config(self):
        config = ScienceConfig()
        assert config.model_name == "qwen3.5-9b"
        assert config.engine_base_url == "http://localhost:11434/v1"
        assert config.api_host == "0.0.0.0"
        assert config.api_port == 8200
        assert config.api_cors_origins == ["*"]

    def test_custom_config(self):
        config = ScienceConfig(model_name="test-model", api_port=9000)
        assert config.model_name == "test-model"
        assert config.api_port == 9000

    def test_load_config_defaults(self):
        config = load_config()
        assert isinstance(config, ScienceConfig)
